"""LanguageSpec for Salesforce Apex classes, triggers and anonymous scripts."""

from ..spec import LanguageSpec

SPEC = LanguageSpec(
    tag="apex",
    display_name="Apex",
    # .apex is an anonymous-Apex script: top-level statements, no class.
    extensions=frozenset({".cls", ".trigger", ".apex"}),
    # The language pack ships the aheber/tree-sitter-sfapex Apex grammar
    # (inline SOQL/SOSL and DML are structured nodes in it), loaded the same
    # way as COBOL.
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
    # Triggers, @AuraEnabled/@InvocableMethod/@RestResource members, platform
    # interfaces (Queueable, Batchable, ...) and global API are entered by the
    # org runtime, Lightning, Flows and managed-package subscribers, none of
    # which the static graph sees yet. Unused/unreachable claims stay off
    # until those entry points are modelled.
    dead_code_exempt=True,
    # FooTest / FooTests. Foo_Test, Foo_TEST and Test_Foo already match the
    # generic stem rules, which compare lowercased.
    test_camel_suffixes=("Test", "Tests"),
    manifest_files=("sfdx-project.json",),
    blocked_dirs=(".sfdx", ".sf"),
    # Platform types written as a constructor target (``new Map<..>`` and
    # ``new List<..>`` are generic and never captured as one). Matched as
    # written; the parser drops the call site.
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
    # Only the root exception type. Platform interfaces (Queueable, Batchable,
    # Schedulable, Comparable, ...) stay in the heritage list on purpose: they
    # are what marks a class's contract methods as runtime-invoked.
    builtin_parents=frozenset({"Exception"}),
    # Primitives, collections and the System, Database and Schema types that
    # never resolve to a class the repository declares. Apex type names are
    # case-insensitive, so readers compare these folded (see type_heads).
    # Standard sObjects (Account, Contact, ...) are absent: they are schema
    # references, not platform classes.
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
