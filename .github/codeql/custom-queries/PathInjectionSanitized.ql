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
 */
class SpecPathSanitizer extends PathInjection::Sanitizer {
  SpecPathSanitizer() {
    this = API::moduleImport("os").getMember("path").getMember("basename").getACall()
    or
    exists(DataFlow::CallCfgNode call, string name |
      name in ["safe_component", "safe_spec_dir", "safe_join", "get_next_spec_id"] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
  }
}

from PathInjectionFlow::PathNode source, PathInjectionFlow::PathNode sink
where PathInjectionFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "This path depends on a $@.", source.getNode(),
  "user-provided value"
