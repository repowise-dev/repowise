"""LanguageSpec for Salesforce Apex classes, triggers and anonymous scripts."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="apex",
    display_name="Apex",
    # .apex is an anonymous-Apex script: top-level statements, no class.
    extensions=frozenset({".cls", ".trigger", ".apex"}),
    # tree-sitter-sfapex from the language pack, loaded as COBOL's is.
    grammar_package="tree_sitter_language_pack",
    grammar_loader="get_language",
    grammar_loader_args=("apex",),
    scm_file="apex.scm",
    heritage_node_types=frozenset(
        {"class_declaration", "interface_declaration", "enum_declaration"}
    ),
    # Apex has no import statement: every top-level class in a package
    # directory is visible by name, so there is nothing for a resolver to read.
    import_support="none",
    # FooTest / FooTests. Foo_Test, Foo_TEST and Test_Foo already match the
    # generic stem rules, which compare lowercased.
    test_camel_suffixes=("Test", "Tests"),
    manifest_files=("sfdx-project.json",),
    blocked_dirs=(".sfdx", ".sf"),
    # Platform types written as a constructor target; the parser drops the call site.
    builtin_calls=frozenset(
        {
            "System", "Database", "Schema", "Test", "Limits", "Math", "JSON",
            "UserInfo", "String", "Integer", "Long", "Decimal", "Double",
            "Boolean", "Date", "Datetime", "Time", "Blob", "Id", "Object",
            "Exception", "DmlException", "QueryException", "AuraHandledException",
            "IllegalArgumentException", "NullPointerException", "ListException",
            "StringException", "TypeException", "CalloutException",
            "SObjectException", "SecurityException", "JSONException",
            "Http", "HttpRequest", "HttpResponse", "PageReference", "Pattern",
            "SelectOption", "Url", "Version", "Savepoint",
        }
    ),
    # Platform interfaces stay in heritage: they mark contract methods as runtime-invoked.
    builtin_parents=frozenset({"Exception"}),
    # Platform types no repo class can be; compared folded. sObjects are schema, not here.
    builtin_types=frozenset(
        {
            # Primitives and the root types
            "Blob", "Boolean", "Date", "Datetime", "Decimal", "Double", "Id",
            "Integer", "Long", "Object", "String", "Time", "SObject",
            # Collections
            "List", "Map", "Set", "Iterator", "Iterable",
            # System namespace
            "System", "Database", "Schema", "Test", "Limits", "Math", "JSON",
            "JSONParser", "JSONGenerator", "JSONToken", "UserInfo", "Type",
            "Http", "HttpRequest", "HttpResponse", "HttpCalloutMock",
            "WebServiceMock", "StubProvider", "PageReference", "ApexPages",
            "SelectOption", "LoggingLevel", "Pattern", "Matcher", "Url",
            "Crypto", "EncodingUtil", "Messaging", "Trigger", "TriggerOperation",
            "Savepoint", "Approval", "Search", "Label", "Site", "Network",
            "EventBus", "RestContext", "RestRequest", "RestResponse", "Version",
            "Address", "Location", "Continuation", "Callable", "Comparable",
            "Queueable", "QueueableContext", "Schedulable", "SchedulableContext",
            "Finalizer", "FinalizerContext",
            # Database namespace
            "SaveResult", "UpsertResult", "DeleteResult", "UndeleteResult",
            "MergeResult", "QueryLocator", "QueryLocatorIterator", "Batchable",
            "BatchableContext", "Stateful", "AllowsCallouts", "DMLOptions",
            "LeadConvert", "LeadConvertResult",
            # Schema namespace
            "SObjectType", "SObjectField", "DescribeSObjectResult",
            "DescribeFieldResult", "PicklistEntry", "DisplayType", "FieldSet",
            "FieldSetMember", "ChildRelationship", "RecordTypeInfo",
            # Exceptions
            "Exception", "DmlException", "QueryException", "AuraHandledException",
            "IllegalArgumentException", "NullPointerException", "ListException",
            "StringException", "TypeException", "CalloutException",
            "SObjectException", "SecurityException", "JSONException",
            "LimitException", "MathException", "NoAccessException",
            "HandledException", "AsyncException", "EmailException",
            "NoDataFoundException", "SerializationException",
            "VisualforceException", "XmlException",
        }
    ),
    color_hex="#1797C0",
)
