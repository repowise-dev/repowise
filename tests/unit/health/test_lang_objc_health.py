"""Health metrics for Objective-C, pinned on code from AFNetworking.

Each number below was counted by hand from the source. A ternary is a CCN
point and a nesting level. Both the C function and the methods measure their
whole span, signature included, for NLOC.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# AFNetworking AFURLRequestSerialization.m AFQueryStringPairsFromKeyAndValue
# (comment shortened), AFURLResponseSerialization.m validateResponse (its
# status code arm and error dictionary dropped), Example Models/Post.m.
_AFNETWORKING = r"""NSArray * AFQueryStringPairsFromKeyAndValue(NSString *key, id value) {
    NSMutableArray *mutableQueryStringComponents = [NSMutableArray array];

    NSSortDescriptor *sortDescriptor = [NSSortDescriptor sortDescriptorWithKey:@"description" ascending:YES selector:@selector(compare:)];

    if ([value isKindOfClass:[NSDictionary class]]) {
        NSDictionary *dictionary = value;
        // Sort dictionary keys to ensure consistent ordering in query string
        for (id nestedKey in [dictionary.allKeys sortedArrayUsingDescriptors:@[ sortDescriptor ]]) {
            id nestedValue = dictionary[nestedKey];
            if (nestedValue) {
                [mutableQueryStringComponents addObjectsFromArray:AFQueryStringPairsFromKeyAndValue((key ? [NSString stringWithFormat:@"%@[%@]", key, nestedKey] : nestedKey), nestedValue)];
            }
        }
    } else if ([value isKindOfClass:[NSArray class]]) {
        NSArray *array = value;
        for (id nestedValue in array) {
            [mutableQueryStringComponents addObjectsFromArray:AFQueryStringPairsFromKeyAndValue([NSString stringWithFormat:@"%@[]", key], nestedValue)];
        }
    } else if ([value isKindOfClass:[NSSet class]]) {
        NSSet *set = value;
        for (id obj in [set sortedArrayUsingDescriptors:@[ sortDescriptor ]]) {
            [mutableQueryStringComponents addObjectsFromArray:AFQueryStringPairsFromKeyAndValue(key, obj)];
        }
    } else {
        [mutableQueryStringComponents addObject:[[AFQueryStringPair alloc] initWithField:key value:value]];
    }

    return mutableQueryStringComponents;
}

@implementation AFHTTPResponseSerializer

- (BOOL)validateResponse:(NSHTTPURLResponse *)response
                    data:(NSData *)data
                   error:(NSError * __autoreleasing *)error
{
    BOOL responseIsValid = YES;
    NSError *validationError = nil;

    if ([response isKindOfClass:[NSHTTPURLResponse class]]) {
        if (self.acceptableContentTypes && ![self.acceptableContentTypes containsObject:[response MIMEType]] &&
            !([response MIMEType] == nil && [data length] == 0)) {
            if ([data length] > 0 && [response URL]) {
                validationError = AFErrorWithUnderlyingError([NSError errorWithDomain:AFURLResponseSerializationErrorDomain code:NSURLErrorCannotDecodeContentData userInfo:nil], validationError);
            }

            responseIsValid = NO;
        }
    }

    if (error && !responseIsValid) {
        *error = validationError;
    }

    return responseIsValid;
}

@end

@implementation Post (NSCoding)

- (void)encodeWithCoder:(NSCoder *)aCoder {
    [aCoder encodeInteger:(NSInteger)self.postID forKey:@"AF.postID"];
    [aCoder encodeObject:self.text forKey:@"AF.text"];
    [aCoder encodeObject:self.user forKey:@"AF.user"];
}

- (instancetype)initWithCoder:(NSCoder *)aDecoder {
    self = [super init];
    if (!self) {
        return nil;
    }

    self.postID = (NSUInteger)[aDecoder decodeIntegerForKey:@"AF.postID"];
    self.text = [aDecoder decodeObjectOfClass:[NSString class] forKey:@"AF.text"];
    self.user = [aDecoder decodeObjectOfClass:[User class] forKey:@"AF.user"];

    return self;
}

+ (BOOL)supportsSecureCoding {
    return YES;
}

@end
"""


def _walk(source: str):
    fcx = walk_file("src/Sample.m", "objectivec", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for Objective-C unavailable")
    return fcx


def test_afnetworking_functions() -> None:
    metrics = {f.name: (f.ccn, f.max_nesting, f.nloc) for f in _walk(_AFNETWORKING).functions}
    assert metrics == {
        # if, for, if, ternary, else if, for, else if, for. The ternary sits
        # inside if > for > if, so it is the fourth level.
        "AFQueryStringPairsFromKeyAndValue": (9, 4, 26),
        # if, if + 3 ``&&`` (one inside the negated group), if + ``&&``,
        # if + ``&&``.
        "validateResponse": (10, 3, 20),
        "encodeWithCoder": (1, 0, 5),
        "initWithCoder": (2, 1, 10),
        "supportsSecureCoding": (1, 0, 3),
    }


def test_c_function_parameters() -> None:
    [fn] = [f for f in _walk(_AFNETWORKING).functions if f.name == "AFQueryStringPairsFromKeyAndValue"]
    assert fn.param_count == 2


def test_method_parameters() -> None:
    params = {f.name: f.param_count for f in _walk(_AFNETWORKING).functions}
    # A three-part selector has three ``method_parameter`` children, a
    # single-part selector one, and a bare selector none.
    assert params["validateResponse"] == 3
    assert params["encodeWithCoder"] == 1
    assert params["supportsSecureCoding"] == 0


def test_compound_condition() -> None:
    conditions = [
        (f.name, c.line, c.operator_count)
        for f in _walk(_AFNETWORKING).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    assert conditions == [("validateResponse", 42, 3)]


def test_class_members() -> None:
    classes = {c.name: (c.method_count, c.total_nloc) for c in _walk(_AFNETWORKING).classes}
    assert classes == {
        "AFHTTPResponseSerializer": (1, 22),
        # A category is measured under the class it extends.
        "Post": (3, 20),
    }
