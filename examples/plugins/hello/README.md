# repowise-hello: a minimal plugin

One package that registers four things: a CLI command (`repowise hello`), an
opt-in MCP tool (`hello_greet`), an event sink (`hello-stderr`) and a report
section (`hello`). Read [src/repowise_hello/\_\_init\_\_.py](src/repowise_hello/__init__.py)
next to the [plugin author guide](../../../docs/reference/PLUGINS.md).

## Install and check it loaded

```bash
pip install -e examples/plugins/hello      # into the same environment as repowise
repowise plugins                           # hello  entry point  repowise_hello:register  loaded
repowise hello                             # hello, world
```

## Try each registration

The report and the sink read an index, so run these in a repository indexed
with `repowise init`.

```bash
repowise mcp --tools "+hello_greet"            # stdio MCP server with the opt-in tool; point your agent at it
repowise report assessment --format markdown   # the "Hello" section runs with the rest
```

To send events to the sink, add it in `.repowise/config.yaml`, then run
`repowise update`. Each delivery prints `repowise-hello: N event(s) for <path>`
on stderr when the run produces component events.

```yaml
events:
  sinks: [jsonl, hello-stderr]
```

## Without installing

```bash
PYTHONPATH=examples/plugins/hello/src REPOWISE_PLUGINS=repowise_hello:register repowise plugins
```
