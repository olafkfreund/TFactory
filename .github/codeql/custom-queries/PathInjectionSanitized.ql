/**
 * @name Uncontrolled data used in path expression (sanitizer-aware)
 * @description Accessing paths influenced by users can allow an attacker to access unexpected resources.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 7.5
 * @sub-severity high
 * @precision high
 * @id py/path-injection-sanitized
 * @tags correctness
 *       security
 *       external/cwe/cwe-022
 *       external/cwe/cwe-023
 *       external/cwe/cwe-036
 *       external/cwe/cwe-073
 *       external/cwe/cwe-099
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.PathInjectionQuery
import semmle.python.security.dataflow.PathInjectionCustomizations
import PathInjectionFlow::PathGraph

/**
 * The result of our spec/path sanitizers is a validated path that cannot escape
 * its intended root (os.path.basename strips directory parts; safe_component
 * rejects non-bare ids; safe_spec_dir/safe_join confirm containment). Treat it
 * as a barrier for path injection.
 *
 * trusted_project_root is different in kind: it does not confine the path.
 * It is the named choke point for the local-install trust model (an
 * authenticated client may open any local directory, like an editor), so a
 * project root routed through it is accepted by explicit decision, not by
 * accident. Barriering it clears every filesystem access derived from a
 * deliberately-accepted project root (issue #664: the five
 * terminal_worktree_service alerts were project-root flows, which
 * safe_component was never on).
 */
class SpecPathSanitizer extends PathInjection::Sanitizer {
  SpecPathSanitizer() {
    this = API::moduleImport("os").getMember("path").getMember("basename").getACall()
    or
    exists(DataFlow::CallCfgNode call, string name |
      name in [
          "safe_component", "safe_spec_dir", "safe_join", "get_next_spec_id",
          "trusted_project_root"
        ] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
    or
    // The body of trusted_project_root is the accept point itself: barrier its
    // parameter so the resolve()/is_dir() probes inside the helper do not
    // re-fire the very alerts the barrier exists to clear.
    exists(Function f |
      f.getName() = "trusted_project_root" and
      this.(DataFlow::ParameterNode).getParameter() = f.getArg(0)
    )
  }
}

from PathInjectionFlow::PathNode source, PathInjectionFlow::PathNode sink
where PathInjectionFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "This path depends on a $@.", source.getNode(),
  "user-provided value"
