"""Health metrics for Ruby, pinned on methods from rack.

Each number below was counted by hand from the source. Ruby blocks
(``each do ... end``) are neither loops nor nesting levels: they roll up into
the method, as ``_RUBY`` in ``complexity/languages.py`` documents. A modifier
``unless`` and a ``||`` in an assignment are decisions too. NLOC is the body,
without the ``def`` and ``end`` lines. Parameters are plain positional ones.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

# rack request.rb forwarded_scheme (its x_forwarded arm), urlmap.rb call
# (trailing comment dropped), multipart/parser.rb on_mime_head, auth/basic.rb.
_RACK = r"""class Request
  def x_forwarded_scheme
    x_forwarded_proto_priority.each do |x_type|
      if header = FORWARDED_SCHEME_HEADERS[x_type]
        if x_type == :ssl && get_header(header) == 'on'
          return 'https'
        else
          split_header(get_header(header)).reverse_each do |scheme|
            if allowed_scheme(scheme)
              return scheme
            end
          end
        end
      end
    end

    nil
  end
end

class URLMap
  def call(env)
    path        = env[PATH_INFO]
    script_name = env[SCRIPT_NAME]
    http_host   = env[HTTP_HOST]
    server_name = env[SERVER_NAME]
    server_port = env[SERVER_PORT]

    is_same_server = casecmp?(http_host, server_name) ||
                     casecmp?(http_host, "#{server_name}:#{server_port}")

    is_host_known = @known_hosts.include? http_host

    @mapping.each do |host, location, match, app|
      unless casecmp?(http_host, host) \
          || casecmp?(server_name, host) \
          || (!host && is_same_server) \
          || (!host && !is_host_known)
        next
      end

      next unless m = match.match(path.to_s)

      rest = m[1]
      next unless !rest || rest.empty? || rest[0] == ?/

      env[SCRIPT_NAME] = (script_name + location)
      env[PATH_INFO] = rest

      return app.call(env)
    end

    [404, { CONTENT_TYPE => "text/plain", "x-cascade" => "pass" }, ["Not Found: #{path}"]]

  ensure
    env[PATH_INFO]   = path
    env[SCRIPT_NAME] = script_name
  end
end

class Parser
  def on_mime_head(mime_index, head, filename, content_type, name)
    check_total_part_limit

    if filename
      # This will raise an exception if we are at the limit:
      check_file_part_limit

      body = @tempfile.call(filename, content_type)
      body.binmode if body.respond_to?(:binmode)
      klass = TempfilePart
      @open_files += 1
    else
      body = String.new
      klass = BufferPart
    end

    @mime_parts[mime_index] = klass.new(body, head, filename, content_type, name)
  end
end

class Basic < AbstractRequest
  def basic?
    "basic" == scheme && credentials.length == 2
  end

  def credentials
    @credentials ||= params.unpack1('m').split(':', 2)
  end

  def username
    credentials.first
  end
end
"""


def _walk(source: str):
    fcx = walk_file("lib/sample.rb", "ruby", source.encode())
    if not fcx.functions:
        pytest.skip("tree-sitter grammar for Ruby unavailable")
    return fcx


def test_rack_methods() -> None:
    metrics = {
        f.name: (f.ccn, f.max_nesting, f.nloc, f.param_count)
        for f in _walk(_RACK).functions
        if f.name != "credentials"
    }
    assert metrics == {
        # if, if + ``&&``, if: blocks add neither CCN nor depth.
        "x_forwarded_scheme": (5, 3, 14, 0),
        # ``||`` in the assignment, unless + ``|| || || && &&``, two modifier
        # unlesses, the second with ``|| ||``.
        "call": (12, 1, 26, 1),
        # if + the modifier if.
        "on_mime_head": (3, 1, 12, 5),
        "basic?": (2, 0, 1, 0),
        "username": (1, 0, 1, 0),
    }


def test_compound_condition() -> None:
    conditions = [
        (f.name, c.line, c.operator_count, c.flat_chain)
        for f in _walk(_RACK).functions
        for c in f.complex_conditions
        if c.operator_count >= 3
    ]
    # The ``unless`` guard, reported under the ``if`` construct.
    assert conditions == [("call", 35, 5, False)]


def test_class_members() -> None:
    classes = {c.name: (c.method_count, c.total_nloc) for c in _walk(_RACK).classes}
    assert classes == {
        "Request": (1, 18),
        "URLMap": (1, 30),
        "Parser": (1, 16),
        "Basic": (3, 11),
    }
