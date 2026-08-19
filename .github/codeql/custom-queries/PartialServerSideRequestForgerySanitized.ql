/**
 * @name Partial server-side request forgery (sanitizer-aware)
 * @description Making a network request to a URL that is partially user-controlled allows for request forgery attacks.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.1
 * @precision medium
 * @id py/partial-ssrf-sanitized
 * @tags security
 *       external/cwe/cwe-918
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.security.dataflow.ServerSideRequestForgeryCustomizations
import semmle.python.security.dataflow.ServerSideRequestForgeryQuery
import PartialServerSideRequestForgeryFlow::PathGraph

/**
 * The outbound-URL guard in `factory_common/url_safety.py` (the hub canonical,
 * vendored; this server ran a forked copy at `services/url_safety.py` until
 * #1111) resolves the host and
 * refuses non-http(s) schemes and the cloud metadata ranges (and, in the strict
 * posture, every non-public address). The route helpers below call it and
 * return a NORMALISED `scheme://netloc` -- path, query and fragment discarded --
 * so the URL that reaches the transport can no longer be steered by the caller
 * beyond the host the guard just approved.
 *
 * Stock `py/partial-ssrf` has no way to know that, so it keeps reporting the
 * sink. This is the same situation, and the same remedy, as
 * `py/path-injection-sanitized` in this directory: teach the query about the
 * sanitizer that actually exists rather than ask a narrower question. The
 * config swaps the stock query for this one -- the query is REPLACED, not
 * removed, so an unguarded call site still reports.
 *
 * Deliberately NOT barriered: `assert_safe_outbound_url` itself. Since #1111
 * this server runs the hub canonical, which DOES return the checked URL, so
 * barriering it would no longer be meaningless -- but every call site here
 * still calls it as a statement and fetches the helper's normalised value, so
 * a barrier on the guard would clear flows this repo has not shown to be
 * sanitised. Only the helpers that return a validated value are barriers, and
 * each one is covered by a posture test in
 * apps/web-server/tests/test_url_safety_guard.py. Registering the guard itself
 * is a separate change, with its own alert-delta evidence, not a free ride on
 * this one.
 */
class ValidatedOutboundUrlSanitizer extends ServerSideRequestForgery::Sanitizer {
  // The class is `ServerSideRequestForgery::Sanitizer` from
  // ServerSideRequestForgeryCustomizations; the query module re-exports the
  // unqualified names (Sink, fullyControlledRequest) used below.
  ValidatedOutboundUrlSanitizer() {
    exists(DataFlow::CallCfgNode call, string name |
      name in [
          "_safe_ollama_base_url", "_safe_local_base_url", "_safe_profile_models_url",
          "_safe_mcp_url", "_safe_probe_models_url"
        ] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
  }
}

from
  PartialServerSideRequestForgeryFlow::PathNode source,
  PartialServerSideRequestForgeryFlow::PathNode sink, Http::Client::Request request
where
  request = sink.getNode().(Sink).getRequest() and
  PartialServerSideRequestForgeryFlow::flowPath(source, sink) and
  not fullyControlledRequest(request)
select request, source, sink, "Part of the URL of this request depends on a $@.", source.getNode(),
  "user-provided value"
