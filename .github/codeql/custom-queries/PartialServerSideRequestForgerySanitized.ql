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
 * The outbound-URL guard in `services/url_safety.py` resolves the host and
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
 * Deliberately NOT barriered: `assert_safe_outbound_url` itself. It validates
 * and returns None, so barriering its call would clear nothing and imply
 * something it does not do. Only the helpers that return a validated value are
 * barriers, and each one is covered by a posture test in
 * apps/web-server/tests/test_url_safety_guard.py.
 */
class ValidatedOutboundUrlSanitizer extends ServerSideRequestForgery::Sanitizer {
  // The class is `ServerSideRequestForgery::Sanitizer` from
  // ServerSideRequestForgeryCustomizations; the query module re-exports the
  // unqualified names (Sink, fullyControlledRequest) used below.
  ValidatedOutboundUrlSanitizer() {
    exists(DataFlow::CallCfgNode call, string name |
      name in [
          "_safe_ollama_base_url", "_safe_local_base_url", "_safe_profile_models_url"
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
