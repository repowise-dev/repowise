"""AWS Lambda handlers named in serverless.yml, SAM templates and Terraform as graph edges.

The cases go through traverse -> parse -> graph -> framework edges -> dead code,
the path a real index takes.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_SERVERLESS_NODE = """\
service: users
provider:
  name: aws
  runtime: nodejs20.x
functions:
  create:
    handler: src/handlers/users.create
  legacy:
    handler: src/handlers/legacy.run
  missing:
    handler: src/handlers/users.doesNotExist
  variable:
    handler: ${self:custom.handler}
  nowhere:
    handler: src/handlers/absent.main
"""

_SERVERLESS_PYTHON = """\
service: reports
provider:
  name: aws
  runtime: python3.12
functions:
  main:
    handler: app.handlers.lambda_handler
  package:
    handler: tasks.worker.process
"""

_SAM_TEMPLATE = """\
AWSTemplateFormatVersion: '2010-09-09'
Transform: AWS::Serverless-2016-10-31
Globals:
  Function:
    Runtime: nodejs20.x
    CodeUri: functions/
Resources:
  IndexFn:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src/
      Handler: index.handler
      Role: !GetAtt Role.Arn
      Environment:
        Variables:
          TABLE: !Ref Table
          URL: !Sub "https://${Api}.example.com"
  AppFn:
    Type: AWS::Serverless::Function
    Properties:
      Handler: app.lambdaHandler
      Policies:
        - !Ref Policy
  Table:
    Type: AWS::DynamoDB::Table
"""

_TF_ARCHIVE_DIR = """\
data "archive_file" "broker" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/broker.zip"
}

resource "aws_lambda_function" "broker" {
  function_name = "broker"
  filename      = data.archive_file.broker.output_path
  handler       = "broker.handler" # the export AWS invokes
  runtime       = "nodejs20.x"
  environment {
    variables = { MODE = "${var.mode}" }
  }
}
"""

# The archive is declared in one file and used in another, as Terraform allows.
_TF_ARCHIVE_FILE_DATA = """\
data "archive_file" "fn" {
  type        = "zip"
  source_file = "${path.module}/example_lambda.js"
  output_path = "${path.module}/example_lambda.js.zip"
}
"""
_TF_ARCHIVE_FILE_FN = """\
resource "aws_lambda_function" "fn" {
  filename = "${data.archive_file.fn.output_path}"
  handler  = "example_lambda.handler"
  assume_role_policy = <<EOF
{ "Version": "2012-10-17" }
EOF
}
"""

# A path Terraform resolves from the working directory, not the module.
_TF_WORKDIR_RELATIVE = """\
data "archive_file" "zip" {
  source_file = "lambda/function_src/index.js"
  output_path = "lambda/function_src/index.zip"
}
resource "aws_lambda_function" "fn" {
  filename = data.archive_file.zip.output_path
  handler  = "index.main"
}
"""

_TF_PREBUILT_ZIPS = """\
resource "aws_lambda_function" "worker" {
  filename = "artifacts/worker.zip"
  handler  = "worker.run"
}
resource "aws_lambda_function" "report" {
  s3_bucket = "artifacts"
  s3_key    = "report.zip"
  handler   = "app.report"
}
"""

_TF_MODULES = """\
module "one" {
  source      = "terraform-aws-modules/lambda/aws"
  handler     = "index.lambda_handler"
  source_path = "../src/fn1"
}
module "two" {
  source  = "terraform-aws-modules/lambda/aws"
  handler = "app.handle"
  source_path = [
    {
      path             = "${path.module}/../src/fn2"
      pip_requirements = true
    }
  ]
}
"""

_TF_UNRESOLVABLE = """\
resource "aws_lambda_function" "from_var" {
  filename = data.archive_file.missing.output_path
  handler  = var.handler
}
resource "aws_lambda_function" "missing_archive" {
  filename = data.archive_file.missing.output_path
  handler  = "broker.handler"
}
resource "aws_lambda_function" "no_such_export" {
  filename = "../lambda.zip"
  handler  = "broker.doesNotExist"
}
module "not_a_lambda" {
  source  = "./whatever"
  handler = "broker.handler"
}
# resource "aws_lambda_function" "commented" {
#   handler = "broker.handler"
# }
"""

_APP = {
    "node-svc/serverless.yml": _SERVERLESS_NODE,
    "node-svc/src/handlers/users.mjs": (
        "import { save } from '../lib/db.mjs';\n"
        "export const create = async (event) => save(format(event));\n"
        "export function format(x) { return x; }\n"
        "export function orphanExport() { return 1; }\n"
    ),
    "node-svc/src/handlers/legacy.js": "module.exports.run = async (event) => event;\n",
    "node-svc/src/lib/db.mjs": "export function save(x) { return x; }\n",
    "node-svc/src/lib/report.mjs": (
        "import { format } from '../handlers/users.mjs';\nexport const render = (x) => format(x);\n"
    ),
    "py-svc/serverless.yml": _SERVERLESS_PYTHON,
    "py-svc/app/__init__.py": "",
    "py-svc/app/handlers.py": (
        "def lambda_handler(event, context):\n    return shared(event)\n\n\n"
        "def shared(x):\n    return x\n\n\n"
        "def unused_py():\n    return 2\n"
    ),
    "py-svc/app/jobs.py": "from app.handlers import shared\n\n\ndef run():\n    return shared(1)\n",
    "py-svc/tasks/__init__.py": "",
    "py-svc/tasks/worker/__init__.py": "def process(event, context):\n    return event\n",
    "sam-app/template.yaml": _SAM_TEMPLATE,
    "sam-app/src/index.mjs": "export const handler = async (e) => e;\n",
    "sam-app/functions/app.mjs": (
        "export const lambdaHandler = async (e) => e;\nexport const helper = (x) => x;\n"
    ),
    "sam-app/functions/use.mjs": "import { helper } from './app.mjs';\nexport const u = helper(1);\n",
    "tf-a/infra/lambda.tf": _TF_ARCHIVE_DIR,
    "tf-a/lambda/broker.mjs": (
        "export const handler = async () => 1;\nexport const stale = () => 2;\n"
    ),
    "tf-b/data.tf": _TF_ARCHIVE_FILE_DATA,
    "tf-b/lambda.tf": _TF_ARCHIVE_FILE_FN,
    "tf-b/example_lambda.js": "exports.handler = async (event) => event;\n",
    "tf-c/iac/lambda/lambda.tf": _TF_WORKDIR_RELATIVE,
    "tf-c/iac/lambda/function_src/index.js": "exports.main = async () => 1;\n",
    "tf-d/main.tf": _TF_PREBUILT_ZIPS,
    "tf-d/artifacts/worker/worker.py": "def run(event, context):\n    return event\n",
    "tf-d/report/app.py": "def report(event, context):\n    return event\n",
    "tf-e/infra/main.tf": _TF_MODULES,
    "tf-e/src/fn1/index.py": (
        "def lambda_handler(event, context):\n    return 1\n\n\ndef orphan_py():\n    return 2\n"
    ),
    "tf-e/src/fn2/app.py": "def handle(event, context):\n    return 1\n",
    "tf-f/infra/bad.tf": _TF_UNRESOLVABLE,
    "tf-f/lambda/broker.mjs": "export const handler = async () => 1;\n",
    # Not a mapping and not valid YAML: both must be skipped quietly.
    "broken/serverless.yml": "functions: [unclosed\n",
    "list/template.yml": "- just\n- a list\n",
}


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _graph(repo: Path) -> tuple[object, set[tuple[str, str, str]]]:
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges([])
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({})
    dead = {(f.kind.value, f.file_path, f.symbol_name or "") for f in report.findings}
    return builder.graph(), dead


def _framework_names(graph, source: str, target: str) -> list[str]:
    data = graph.get_edge_data(source, target) or {}
    assert data.get("edge_type") == "framework", data
    return data["imported_names"]


def test_node_handler_by_file_path_and_export(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    names = _framework_names(graph, "node-svc/serverless.yml", "node-svc/src/handlers/users.mjs")
    assert names == ["create"]
    assert ("unused_export", "node-svc/src/handlers/users.mjs", "create") not in dead
    # The rest of the handler file is still judged on its own uses.
    assert ("unused_export", "node-svc/src/handlers/users.mjs", "orphanExport") in dead


def test_commonjs_handler_keeps_its_file_reachable(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "node-svc/serverless.yml", "node-svc/src/handlers/legacy.js") == [
        "run"
    ]
    assert ("unreachable_file", "node-svc/src/handlers/legacy.js", "") not in dead


def test_python_handler_by_dotted_module(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "py-svc/serverless.yml", "py-svc/app/handlers.py") == [
        "lambda_handler"
    ]
    binds = graph.get_edge_data(
        "py-svc/serverless.yml::__module__", "py-svc/app/handlers.py::lambda_handler"
    )
    assert binds["edge_type"] == "framework_binds"
    assert ("unused_export", "py-svc/app/handlers.py", "lambda_handler") not in dead
    assert ("unused_export", "py-svc/app/handlers.py", "unused_py") in dead


def test_python_handler_in_a_package_init(tmp_path: Path) -> None:
    graph, _dead = _graph(_write(tmp_path, _APP))
    target = "py-svc/tasks/worker/__init__.py"
    assert _framework_names(graph, "py-svc/serverless.yml", target) == ["process"]
    binds = graph.get_edge_data("py-svc/serverless.yml::__module__", f"{target}::process")
    assert binds["edge_type"] == "framework_binds"


def test_sam_code_uri_and_globals(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "sam-app/template.yaml", "sam-app/src/index.mjs") == ["handler"]
    assert _framework_names(graph, "sam-app/template.yaml", "sam-app/functions/app.mjs") == [
        "lambdaHandler"
    ]
    assert ("unused_export", "sam-app/src/index.mjs", "handler") not in dead
    assert ("unused_export", "sam-app/functions/app.mjs", "lambdaHandler") not in dead


def _lambda_targets(graph, config: str) -> set[str]:
    return {
        target
        for source in (config, f"{config}::__module__")
        for _, target, data in graph.out_edges(source, data=True)
        if data.get("edge_type") in ("framework", "framework_binds")
    }


def test_unresolvable_handlers_add_nothing(tmp_path: Path) -> None:
    graph, _dead = _graph(_write(tmp_path, _APP))
    # A function the file does not define, a variable and a missing file.
    names = _framework_names(graph, "node-svc/serverless.yml", "node-svc/src/handlers/users.mjs")
    assert "doesNotExist" not in names
    assert _lambda_targets(graph, "node-svc/serverless.yml") == {
        "node-svc/src/handlers/users.mjs",
        "node-svc/src/handlers/users.mjs::create",
        "node-svc/src/handlers/legacy.js",
    }
    # Invalid YAML and a top-level list are skipped without raising.
    assert _lambda_targets(graph, "broken/serverless.yml") == set()
    assert _lambda_targets(graph, "list/template.yml") == set()


def test_terraform_archive_source_dir(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "tf-a/infra/lambda.tf", "tf-a/lambda/broker.mjs") == ["handler"]
    binds = graph.get_edge_data("tf-a/infra/lambda.tf::__module__", "tf-a/lambda/broker.mjs::handler")
    assert binds["edge_type"] == "framework_binds"
    assert ("unused_export", "tf-a/lambda/broker.mjs", "handler") not in dead
    assert ("unused_export", "tf-a/lambda/broker.mjs", "stale") in dead


def test_terraform_archive_source_file_across_files(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "tf-b/lambda.tf", "tf-b/example_lambda.js") == ["handler"]
    assert ("unreachable_file", "tf-b/example_lambda.js", "") not in dead


def test_terraform_path_relative_to_working_directory(tmp_path: Path) -> None:
    graph, _dead = _graph(_write(tmp_path, _APP))
    target = "tf-c/iac/lambda/function_src/index.js"
    assert _framework_names(graph, "tf-c/iac/lambda/lambda.tf", target) == ["main"]


def test_terraform_prebuilt_zip_and_s3_key(tmp_path: Path) -> None:
    graph, _dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "tf-d/main.tf", "tf-d/artifacts/worker/worker.py") == ["run"]
    assert _framework_names(graph, "tf-d/main.tf", "tf-d/report/app.py") == ["report"]


def test_terraform_lambda_module_source_path(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    assert _framework_names(graph, "tf-e/infra/main.tf", "tf-e/src/fn1/index.py") == [
        "lambda_handler"
    ]
    assert _framework_names(graph, "tf-e/infra/main.tf", "tf-e/src/fn2/app.py") == ["handle"]
    assert ("unused_export", "tf-e/src/fn1/index.py", "orphan_py") in dead


def test_terraform_unresolvable_handlers_add_nothing(tmp_path: Path) -> None:
    graph, _dead = _graph(_write(tmp_path, _APP))
    # A variable, a missing archive, an export the file lacks, a module that is
    # not a Lambda and a commented-out block.
    assert _lambda_targets(graph, "tf-f/infra/bad.tf") == set()
