"""Unit tests for the Objective-C language pipeline.

Tests parse inline byte strings so no filesystem I/O is needed. Covers
symbols (including joined selector names, categories and class extensions),
imports, type references, message sends, heritage, docstrings and the
whole-line macro sanitiser -- see docs/architecture/language-support.md's
"one .scm file + one LanguageConfig" recipe.
"""

from __future__ import annotations

import tree_sitter_objc  # noqa: F401  -- a real dependency; the import proves it

from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.parser_helpers import prepare_objectivec_source
from tests.unit.ingestion.parser._helpers import _make_file_info


def _objc(path: str = "Foo.m") -> object:
    return _make_file_info(path, "objectivec")


HEADER = b"""\
#import <Foundation/Foundation.h>
#import "Helper.h"
#include <stdio.h>
@class Sidecar;

NS_ASSUME_NONNULL_BEGIN

@protocol Feeder <NSObject>
- (void)didFinish:(id)sender;
@end

/** The widget everything hangs off. */
@interface Widget : Panel <Feeder, NSCopying>
@property (nonatomic, strong) Sidecar *sidecar;
@property (nonatomic) int slot;
- (instancetype)initWithName:(NSString *)name age:(NSInteger)age;
+ (instancetype)widgetWithName:(NSString *)name;
- (void)run;
@end

@interface Widget (Trimming)
- (void)trim;
@end

NS_ASSUME_NONNULL_END
"""

IMPLEMENTATION = b"""\
#import "Widget.h"

@interface Widget ()
- (void)secret;
@end

@implementation Widget

- (instancetype)initWithName:(NSString *)name age:(NSInteger)age {
    self = [super init];
    NSLog(@"%@", name);
    normalise(3);
    [self secret];
    return [[Sidecar alloc] initWithName:name age:0];
}

- (void)run {}
- (void)secret {}

@end

static int normalise(int x) { return x; }
"""


class TestObjectiveCSymbols:
    def test_finds_the_top_level_kinds(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("Widget", "class") in kinds
        assert ("Feeder", "interface") in kinds
        assert ("sidecar", "variable") in kinds
        assert ("slot", "variable") in kinds

    def test_a_method_is_named_by_its_whole_selector(self, parser: ASTParser) -> None:
        # The grammar has no selector node: `initWithName:age:` is a flat run
        # of bare identifiers interleaved with method_parameter nodes, and the
        # query can only capture the first. Without the join, this method and
        # any other method starting `initWithName:` collide on one symbol id.
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        names = {s.name for s in result.symbols}
        assert "initWithName:age:" in names
        assert "widgetWithName:" in names
        # A method taking no arguments carries no colon at all.
        assert "run" in names

    def test_methods_belong_to_their_interface(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        owned = {(s.name, s.parent_name) for s in result.symbols}
        assert ("initWithName:age:", "Widget") in owned
        assert ("run", "Widget") in owned
        assert ("didFinish:", "Feeder") in owned

    def test_a_category_is_a_symbol_of_its_own(self, parser: ASTParser) -> None:
        # Two categories on one class are two declarations, so naming both for
        # the class alone would merge them and hide the second.
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        names = {s.name for s in result.symbols}
        assert "Widget(Trimming)" in names
        assert ("trim", "Widget(Trimming)") in {(s.name, s.parent_name) for s in result.symbols}

    def test_a_class_extension_does_not_duplicate_the_implementation(
        self, parser: ASTParser
    ) -> None:
        # A .m opens with `@interface Widget ()` declaring the private methods
        # its `@implementation Widget` then defines. Both build the same symbol
        # id, so the declaration must fold into the definition.
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        widgets = [s for s in result.symbols if s.name == "Widget"]
        assert len(widgets) == 1
        assert widgets[0].is_declaration is False
        # The survivor is the definition, which is what carries the body.
        secrets = [s for s in result.symbols if s.name == "secret"]
        assert len(secrets) == 1
        assert secrets[0].is_declaration is False

    def test_a_header_and_a_source_are_two_files_and_two_symbols(
        self, parser: ASTParser
    ) -> None:
        # The C/C++ precedent: a declaration and its definition live in two
        # files and stay two symbols, told apart by is_declaration rather than
        # merged by a cross-file pass.
        header = parser.parse_file(_objc("Widget.h"), HEADER)
        impl = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        declared = next(s for s in header.symbols if s.name == "Widget")
        defined = next(s for s in impl.symbols if s.name == "Widget")
        assert declared.is_declaration is True
        assert defined.is_declaration is False
        assert declared.id != defined.id

    def test_plain_c_in_a_dot_m_file_is_a_symbol(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        assert ("normalise", "function") in {(s.name, s.kind) for s in result.symbols}

    def test_non_ascii_round_trips(self, parser: ASTParser) -> None:
        src = "@implementation Café\n- (void)prêt {}\n@end\n".encode()
        result = parser.parse_file(_objc(), src)
        names = {s.name for s in result.symbols}
        assert "Café" in names
        assert "prêt" in names


class TestObjectiveCImports:
    def test_both_import_forms_and_include(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        modules = {i.module_path for i in result.imports}
        assert "<Foundation/Foundation.h>" in modules
        assert "Helper.h" in modules
        assert "<stdio.h>" in modules

    def test_a_forward_declaration_is_never_an_import(self, parser: ASTParser) -> None:
        # `@class Sidecar;` names a class, not a file. It has no path to
        # resolve, so treating it as an import would mint a broken edge.
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        assert "Sidecar" not in {i.module_path for i in result.imports}


class TestObjectiveCTypeReferences:
    def test_a_forward_declared_class_is_a_type_reference(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        names = {t.type_name for t in result.type_refs}
        assert "Sidecar" in names
        assert "Feeder" in names

    def test_a_forward_declaration_is_never_a_call(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        assert "Sidecar" not in {c.target_name for c in result.calls}

    def test_foundation_types_are_filtered(self, parser: ASTParser) -> None:
        # NSString has no in-repo declaration, so resolving it is a
        # guaranteed miss.
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        assert "NSString" not in {t.type_name for t in result.type_refs}


class TestObjectiveCCalls:
    def test_a_message_send_carries_its_whole_selector(self, parser: ASTParser) -> None:
        # `[[Sidecar alloc] initWithName:name age:0]` binds one `method:`
        # child per keyword, so the query matches twice. Only the joined
        # selector is a real target, and it has to match the symbol side.
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        targets = [c.target_name for c in result.calls]
        assert "initWithName:age:" in targets
        assert "age" not in targets

    def test_a_nested_send_is_two_call_sites(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        by_target = {c.target_name: c for c in result.calls}
        assert by_target["alloc"].receiver_name == "Sidecar"
        assert by_target["initWithName:age:"].receiver_name == "[Sidecar alloc]"

    def test_self_and_super_keep_their_text(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        receivers = {(c.target_name, c.receiver_name) for c in result.calls}
        assert ("init", "super") in receivers
        assert ("secret", "self") in receivers

    def test_a_plain_c_call_is_captured(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        assert "normalise" in {c.target_name for c in result.calls}

    def test_a_runtime_function_mints_no_edge(self, parser: ASTParser) -> None:
        # NSLog is a Foundation function and can never resolve in-repo.
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        assert "NSLog" not in {c.target_name for c in result.calls}

    def test_calls_are_attributed_to_the_enclosing_method(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.m"), IMPLEMENTATION)
        call = next(c for c in result.calls if c.target_name == "normalise")
        assert call.caller_symbol_id == "Widget.m::Widget::initWithName:age:"


class TestObjectiveCHeritage:
    def test_superclass_and_protocols(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        pairs = {(h.parent_name, h.kind) for h in result.heritage if h.child_name == "Widget"}
        assert ("Panel", "extends") in pairs
        assert ("Feeder", "implements") in pairs
        assert ("NSCopying", "implements") in pairs

    def test_a_universal_base_is_skipped(self, parser: ASTParser) -> None:
        # NSObject has no in-repo declaration, so the edge would resolve to
        # nothing -- the same filter Python applies to `object`.
        src = b"@interface Thing : NSObject\n@end\n"
        result = parser.parse_file(_objc("Thing.h"), src)
        assert [h for h in result.heritage if h.parent_name == "NSObject"] == []

    def test_a_category_emits_no_heritage(self, parser: ASTParser) -> None:
        # A category has no superclass in this position; the class it extends
        # is already in its own name.
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        assert [h for h in result.heritage if h.child_name.startswith("Widget(")] == []


class TestObjectiveCDocstrings:
    def test_a_doxygen_block_before_an_interface(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        widget = next(s for s in result.symbols if s.name == "Widget")
        assert "widget everything hangs off" in (widget.docstring or "")

    def test_line_doc_comments_before_a_method(self, parser: ASTParser) -> None:
        src = b"""\
@interface Thing : Panel
/// Resets the thing.
- (void)reset;
@end
"""
        result = parser.parse_file(_objc("Thing.h"), src)
        reset = next(s for s in result.symbols if s.name == "reset")
        assert "Resets the thing." in (reset.docstring or "")


class TestObjectiveCSourceSanitiser:
    def test_the_wrapped_interface_survives(self, parser: ASTParser) -> None:
        # NS_ASSUME_NONNULL_BEGIN is not a preprocessor directive and this
        # grammar has no rule for it, so unsanitised it reads as the start of
        # a C declaration and swallows the whole interface into an ERROR node.
        result = parser.parse_file(_objc("Widget.h"), HEADER)
        assert result.parse_errors == []
        assert "Widget" in {s.name for s in result.symbols}

    def test_every_other_byte_keeps_its_offset(self) -> None:
        src = b"NS_ASSUME_NONNULL_BEGIN\n@interface Thing : Panel\n@end\n"
        out = prepare_objectivec_source(src)
        assert len(out) == len(src)
        assert out.count(b"\n") == src.count(b"\n")
        assert out.startswith(b"                       \n")

    def test_a_macro_that_opens_a_declaration_is_untouched(self) -> None:
        # FOUNDATION_EXPORT leads a real extern declaration the grammar
        # parses; blanking the line would delete the declaration with it.
        src = b'FOUNDATION_EXPORT NSString *const kThingKey;\n'
        assert prepare_objectivec_source(src) == src

    def test_a_file_without_the_macro_is_returned_unchanged(self) -> None:
        src = b"@interface Thing : Panel\n@end\n"
        assert prepare_objectivec_source(src) is src


TRAILING_MACRO_PROPERTY = b"""\
#import "Manager.h"

@interface Manager : NSObject
- (instancetype)initWithTask:(NSURLSessionTask *)task;
@property (nonatomic, strong) NSMutableData *mutableData;
#if AF_CAN_INCLUDE_SESSION_TASK_METRICS
@property (nonatomic, strong) NSURLSessionTaskMetrics *sessionMetrics
    AF_API_AVAILABLE(ios(10), macosx(10.12), watchos(3), tvos(10));
#endif
@property (nonatomic, copy) Handler completionHandler;
- (void)run;
@end

@implementation Manager
- (instancetype)initWithTask:(NSURLSessionTask *)task {
    _mutableData = [NSMutableData data];
    return self;
}

- (void)run {
    normalise(1);
}
@end

static int normalise(int x) { return x; }
"""

TRAILING_MACRO_METHOD = b"""\
#import "Manager.h"

static int normalise(int x) { return x; }

@interface Manager : NSObject
#if AF_CAN_INCLUDE_SESSION_TASK_METRICS
@property (nonatomic, strong) NSURLSessionTaskMetrics *sessionMetrics
    AF_API_AVAILABLE(ios(10), macosx(10.12), watchos(3), tvos(10));
#endif
@end

@implementation Manager
- (void)collectMetrics:(NSURLSessionTaskMetrics *)metrics
    AF_API_AVAILABLE(ios(10), macosx(10.12), watchos(3), tvos(10))
{
    normalise(1);
}
@end
"""


class TestObjectiveCTrailingAttributeMacros:
    def test_a_trailing_property_macro_does_not_derail_the_rest_of_the_file(
        self, parser: ASTParser
    ) -> None:
        # The macro is the last token of a declaration the grammar otherwise
        # reads fine, so it cannot be blanked as a whole line. Left in, it
        # reads as an unclosable declarator, recovery never resyncs, and the
        # damage runs from the property through the class and its method
        # bodies: only the property and the method that precede it survive.
        result = parser.parse_file(_objc("Manager.m"), TRAILING_MACRO_PROPERTY)
        assert result.parse_errors == []
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("Manager", "class") in kinds
        assert ("sessionMetrics", "variable") in kinds
        assert ("completionHandler", "variable") in kinds
        assert ("run", "method") in kinds

    def test_a_trailing_method_macro_keeps_the_enclosing_method_as_caller(
        self, parser: ASTParser
    ) -> None:
        # Same cascade, one declaration later: the method body lands outside
        # any function the parser recognises, so its call carries no enclosing
        # symbol and call resolution credits it to the per-file ``__module__``
        # node instead of the method that really makes the call.
        result = parser.parse_file(_objc("Manager.m"), TRAILING_MACRO_METHOD)
        assert result.parse_errors == []
        call = next(c for c in result.calls if c.target_name == "normalise")
        assert call.caller_symbol_id == "Manager.m::Manager::collectMetrics:"
        assert all("__module__" not in (c.caller_symbol_id or "") for c in result.calls)

    def test_the_macro_is_blanked_and_every_other_byte_keeps_its_offset(self) -> None:
        src = (
            b"@property (nonatomic, copy) Block done\n"
            b"    AF_API_AVAILABLE(ios(10), macosx(10.12), watchos(3), tvos(10));\n"
            b"- (void)run NS_SWIFT_NAME(run());\n"
        )
        out = prepare_objectivec_source(src)
        assert len(out) == len(src)
        assert out.count(b"\n") == src.count(b"\n")
        assert b"AF_API_AVAILABLE" not in out
        assert b"NS_SWIFT_NAME" not in out
        # The terminator each declaration ends with is still there.
        assert out.count(b";") == src.count(b";")

    def test_a_method_definition_may_put_its_brace_on_the_next_line(self) -> None:
        src = b"- (void)collectMetrics:(id)metrics\n    NS_AVAILABLE(10_0)\n{\n}\n"
        out = prepare_objectivec_source(src)
        assert b"NS_AVAILABLE" not in out
        assert out.endswith(b"{\n}\n")

    def test_the_same_name_used_as_a_value_is_untouched(self) -> None:
        # Only a form that terminates a declaration is an attribute; here the
        # macro name sits inside an argument list and its parentheses close
        # before anything that could end a declaration.
        src = b'log(NS_SWIFT_NAME(x), 2);\nfoo(API_AVAILABLE(ios(10)), 2);\n'
        assert prepare_objectivec_source(src) == src

    def test_a_longer_identifier_ending_in_the_macro_name_is_untouched(self) -> None:
        assert prepare_objectivec_source(b"MY_API_AVAILABLE(10);\n") == b"MY_API_AVAILABLE(10);\n"

    def test_an_enum_macro_is_still_untouched(self) -> None:
        # NS_ENUM leads a declaration whose body the grammar needs; blanking
        # it would take the enum with it. It is the reason this list is
        # enumerated by name rather than matching any IDENT(...) before a brace.
        src = b"typedef NS_ENUM(NSInteger, Kind) { KindA, KindB };\n"
        assert prepare_objectivec_source(src) == src


BLOCK_INVOCATION = b"""\
@interface Token : NSObject
@property (nonatomic, copy) SDBlock completionBlock;
@property (nonatomic, copy) SDBlock doneBlock;
@end

@implementation Cache
- (void)existsForKey:(NSString *)key completion:(SDBlock)completionBlock {
    SDBlock doneBlock = ^{};
    completionBlock(YES);
    doneBlock(nil);
    normalise(1);
}
@end
"""


class TestObjectiveCBlockInvocation:
    def test_a_block_parameter_call_is_not_an_edge(self, parser: ASTParser) -> None:
        # `completionBlock(YES)` invokes the method's own block parameter. It
        # is a call_expression on a bare identifier, indistinguishable from a
        # C function call by name, so left in, the resolver bound it to
        # whatever same-named @property the repo held.
        result = parser.parse_file(_objc("Cache.m"), BLOCK_INVOCATION)
        assert "completionBlock" not in {c.target_name for c in result.calls}

    def test_a_block_local_call_is_not_an_edge(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Cache.m"), BLOCK_INVOCATION)
        assert "doneBlock" not in {c.target_name for c in result.calls}

    def test_a_real_c_call_in_the_same_body_survives(self, parser: ASTParser) -> None:
        # The scope check must not swallow every bare-identifier call.
        result = parser.parse_file(_objc("Cache.m"), BLOCK_INVOCATION)
        assert "normalise" in {c.target_name for c in result.calls}

    def test_the_property_itself_is_still_a_symbol(self, parser: ASTParser) -> None:
        result = parser.parse_file(_objc("Cache.m"), BLOCK_INVOCATION)
        assert ("completionBlock", "Token") in {
            (s.name, s.parent_name) for s in result.symbols
        }


class TestObjectiveCSelectorAttributes:
    def test_a_trailing_macro_is_not_part_of_the_selector(self, parser: ASTParser) -> None:
        # `initWithName:NS_DESIGNATED_INITIALIZER:` never pairs with the
        # `initWithName:` the .m defines, so the header and the implementation
        # of one method read as two unrelated symbols.
        src = b"""\
@interface Widget : NSObject
- (instancetype)initWithName:(NSString *)name NS_DESIGNATED_INITIALIZER;
- (void)sup:(int)a NS_REQUIRES_SUPER;
- (void)swiftly:(int)a NS_SWIFT_NAME(sw(a:));
- (void)avail:(int)a NS_AVAILABLE_IOS(10_0);
- (void)attr:(int)a __attribute__((deprecated));
@end
"""
        result = parser.parse_file(_objc("Widget.h"), src)
        names = {s.name for s in result.symbols if s.kind == "method"}
        assert names == {"initWithName:", "sup:", "swiftly:", "avail:", "attr:"}

    def test_an_unnamed_keyword_part_adds_no_colon(self, parser: ASTParser) -> None:
        # `- (void)baz:(int)a :(int)b` is the selector `baz::` to the compiler,
        # but the grammar gives the second part no keyword identifier at all,
        # so only the colons it can see are counted.
        src = b"@interface Widget : NSObject\n- (void)baz:(int)a :(int)b;\n@end\n"
        result = parser.parse_file(_objc("Widget.h"), src)
        assert "baz:" in {s.name for s in result.symbols}

    def test_a_header_selector_matches_its_implementation(self, parser: ASTParser) -> None:
        header = parser.parse_file(
            _objc("Widget.h"),
            b"@interface Widget : NSObject\n"
            b"- (instancetype)initWithName:(NSString *)n NS_DESIGNATED_INITIALIZER;\n@end\n",
        )
        impl = parser.parse_file(
            _objc("Widget.m"),
            b"@implementation Widget\n- (instancetype)initWithName:(NSString *)n { return self; }\n@end\n",
        )
        declared = next(s for s in header.symbols if s.kind == "method")
        defined = next(s for s in impl.symbols if s.kind == "method")
        assert declared.name == defined.name == "initWithName:"


class TestObjectiveCMacroEnum:
    def test_an_ns_enum_typedef_mints_no_symbol(self, parser: ASTParser) -> None:
        # The grammar has no rule for the macro, so the type name and the brace
        # body land in ERROR nodes and only the enum *cases* survive as
        # declarators -- each becoming a symbol named as though it were the
        # type. No symbol beats a wrong one.
        src = b"""\
typedef NS_ENUM(NSInteger, Kind) { KindA, KindB };
typedef NS_OPTIONS(NSUInteger, Mask) { MaskNone = 0 };
typedef int Plain;
"""
        result = parser.parse_file(_objc("Kind.h"), src)
        names = {s.name for s in result.symbols}
        assert "KindA" not in names
        assert "MaskNone" not in names
        # An ordinary typedef is untouched.
        assert "Plain" in names


class TestObjectiveCProtocolAndClassCollision:
    def test_a_protocol_method_survives_a_same_named_class_method(
        self, parser: ASTParser
    ) -> None:
        # A protocol and a class may share a name in one file, and `-ping` on
        # the protocol is a different method from `-ping` on the class. Keyed
        # on the name alone, the class definition deduped the protocol's
        # declaration away.
        src = b"""\
@protocol Widget <NSObject>
- (void)ping;
@end

@implementation Widget
- (void)ping {}
@end
"""
        result = parser.parse_file(_objc("Widget.m"), src)
        pings = [s for s in result.symbols if s.name == "ping"]
        assert len(pings) == 2
        assert {p.is_declaration for p in pings} == {True, False}

    def test_a_class_extension_still_dedupes(self, parser: ASTParser) -> None:
        # The container kind in the key must not stop the case the dedup
        # exists for: both of these are declared on a class.
        src = b"""\
@interface Widget ()
- (void)secret;
@end

@implementation Widget
- (void)secret {}
@end
"""
        result = parser.parse_file(_objc("Widget.m"), src)
        assert len([s for s in result.symbols if s.name == "secret"]) == 1


class TestObjectiveCConditionalCompilation:
    def test_a_method_inside_a_preproc_if_keeps_its_symbol_and_callers(
        self, parser: ASTParser
    ) -> None:
        # Conditionally compiled methods are the norm in cross-platform
        # Objective-C; a call inside one must not be attributed to the module.
        src = b"""\
@implementation Manager
#if !TARGET_OS_WATCH
- (instancetype)initWithConfiguration:(NSURLSessionConfiguration *)configuration {
    self.reachability = [AFNetworkReachabilityManager sharedManager];
    return self;
}
#endif
@end
"""
        result = parser.parse_file(_objc("Manager.m"), src)
        assert ("initWithConfiguration:", "Manager") in {
            (s.name, s.parent_name) for s in result.symbols
        }
        call = next(c for c in result.calls if c.target_name == "sharedManager")
        assert call.caller_symbol_id == "Manager.m::Manager::initWithConfiguration:"
