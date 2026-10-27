"""LanguageSpec for Objective-C.

Grammar: tree-sitter-objc (PyPI ``tree-sitter-objc``, import name
``tree_sitter_objc``), a superset of the C grammar, so ordinary C
declarations, calls and preprocessor directives inside a ``.m`` file parse
with the same node shapes ``c.scm`` targets.

Two shapes drive the rest of this entry:

* A method's name is its whole selector (``initWithName:age:``), which the
  grammar never puts in one node. ``queries/objectivec.scm`` captures the
  first keyword and ``_objc_selector_name`` joins the rest.
* ``.h`` is claimed by the C++ spec, and one extension maps to one tag
  globally. An Objective-C header is recognised by its content instead, in
  the traverser, right after the extension lookup.

``heritage_node_types`` names only ``class_interface``: the superclass and
the protocol list are declared on the ``@interface``, and an
``@implementation`` repeats neither.
"""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="objectivec",
    display_name="Objective-C",
    # ``.h`` is deliberately not claimed here. It belongs to the C++ spec
    # globally, and a second claim on it would flip every C and C++ header in
    # every repo to this grammar. Objective-C headers are routed by content.
    extensions=frozenset({".m", ".mm"}),
    grammar_package="tree_sitter_objc",
    scm_file="objectivec.scm",
    heritage_node_types=frozenset({"class_interface"}),
    # No dedicated resolver. ``#import "Foo.h"`` resolves through the generic
    # stem map, which covers the near-universal one-class-per-file naming
    # convention; a framework import (``<Foundation/Foundation.h>``) names no
    # in-repo file and correctly resolves to nothing.
    import_support="none",
    # main.m holds UIApplicationMain/NSApplicationMain.
    entry_point_patterns=("main.m",),
    # XCTest names a test file for the class under test plus a ``Tests``
    # suffix -- ``FooTests.m`` for ``Foo.m``.
    test_camel_suffixes=("Tests", "Test"),
    # Foundation / libSystem / runtime functions. Each is an ordinary C call
    # in the AST, so without this set every one mints an edge to a target
    # that can never exist in the repo. Matched receiver-blind, so the set
    # stays to names no project would define itself.
    builtin_calls=frozenset({
        "NSLog", "NSAssert", "NSCAssert", "NSParameterAssert",
        "NSCParameterAssert", "NSStringFromClass", "NSStringFromSelector",
        "NSStringFromProtocol", "NSClassFromString", "NSSelectorFromString",
        "NSProtocolFromString", "NSMakeRange", "NSLocalizedString",
        "NSLocalizedStringFromTable", "NSLocalizedStringWithDefaultValue",
        "NSApplicationMain", "UIApplicationMain",
        "CFRetain", "CFRelease", "CFAutorelease", "CFBridgingRetain",
        "CFBridgingRelease", "CFEqual", "CFGetTypeID",
        "objc_msgSend", "objc_msgSendSuper", "objc_getClass",
        "objc_getAssociatedObject", "objc_setAssociatedObject",
        "class_getName", "sel_registerName", "sel_getName",
        "method_exchangeImplementations", "class_addMethod",
        "class_getInstanceMethod", "class_getClassMethod",
        "dispatch_async", "dispatch_sync", "dispatch_once",
        "dispatch_after", "dispatch_group_async", "dispatch_group_notify",
        "dispatch_get_main_queue", "dispatch_get_global_queue",
        "dispatch_queue_create", "dispatch_semaphore_create",
        "dispatch_semaphore_wait", "dispatch_semaphore_signal",
        "free", "malloc", "calloc", "realloc", "memcpy", "memset",
        "strlen", "strcmp", "printf", "fprintf", "sprintf", "snprintf",
    }),
    # Universal Foundation / UIKit / AppKit roots. A class extending one of
    # these has no in-repo parent to resolve, so the heritage edge is dropped
    # the same way ``object`` is dropped from Python heritage.
    builtin_parents=frozenset({
        "NSObject", "NSProxy",
        "UIView", "UIViewController", "UIControl", "UIButton", "UILabel",
        "UITableViewCell", "UICollectionViewCell", "UITableViewController",
        "UICollectionViewController", "UINavigationController",
        "UIResponder", "UIWindow", "UIApplication",
        "NSView", "NSViewController", "NSWindowController", "NSDocument",
        "NSOperation", "NSOperationQueue", "NSThread", "NSFormatter",
        "NSValueTransformer", "NSURLProtocol", "NSIncrementalStore",
        "NSManagedObject", "NSPersistentStore", "NSCoder",
        "NSArray", "NSMutableArray", "NSDictionary", "NSMutableDictionary",
        "NSString", "NSMutableString", "NSNumber", "NSError",
    }),
    # Foundation / CoreGraphics types plus the C primitives. None of these
    # ever has an in-repo declaration, so resolving one as a type reference
    # is a guaranteed miss. Read back through ``is_resolvable_type_name``.
    builtin_types=frozenset({
        "id", "instancetype", "Class", "SEL", "IMP", "Protocol", "BOOL",
        "void", "char", "short", "int", "long", "float", "double", "signed",
        "unsigned", "size_t", "ssize_t", "ptrdiff_t", "uintptr_t", "intptr_t",
        "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t",
        "NSInteger", "NSUInteger", "CGFloat", "NSTimeInterval",
        "NSString", "NSMutableString", "NSNumber", "NSValue", "NSData",
        "NSMutableData", "NSArray", "NSMutableArray", "NSDictionary",
        "NSMutableDictionary", "NSSet", "NSMutableSet", "NSOrderedSet",
        "NSDate", "NSURL", "NSURLRequest", "NSURLResponse", "NSURLSession",
        "NSError", "NSException", "NSObject", "NSRange", "NSNotification",
        "NSBundle", "NSFileManager", "NSLock", "NSCoder", "NSIndexPath",
        "CGRect", "CGPoint", "CGSize", "CGAffineTransform", "CGColorRef",
        "dispatch_queue_t", "dispatch_group_t", "dispatch_semaphore_t",
        "dispatch_block_t", "dispatch_time_t",
    }),
    color_hex="#438EFF",
)
