/**
 * @name Uncontrolled command line (sanitizer-aware)
 * @description Using externally controlled strings in a command line allows a malicious user to change the meaning of the command.
 * @kind path-problem
 * @problem.severity error
 * @security-severity 9.8
 * @sub-severity high
 * @precision high
 * @id py/command-line-injection-sanitized
 * @tags correctness
 *       security
 *       external/cwe/cwe-078
 *       external/cwe/cwe-088
 */

import python
import semmle.python.dataflow.new.DataFlow
import semmle.python.ApiGraphs
import semmle.python.security.dataflow.CommandInjectionQuery
import semmle.python.security.dataflow.CommandInjectionCustomizations
import CommandInjectionFlow::PathGraph

/**
 * Same swap as `PathInjectionSanitized.ql` next door (see `codeql-config.yml`):
 * stock CodeQL does not follow a validator that raises instead of returning a
 * clean value.
 *
 * `_safe_arg` (routes/worktree_tools.py) fullmatches `[\w./:\\ -]+`, so the
 * value holds no shell metacharacter. It is honest as the only barrier because
 * it sees only the worktree path, already confined by `_validated_worktree_path`,
 * and every Popen there is list-form with no shell. The request-chosen launcher
 * (`customPath`) is refused with HTTP 400 before any of this (Factory#813); a
 * `_safe_arg` barrier on an attacker-chosen program name would hide a real RCE,
 * so never land this twin without that refusal.
 *
 * `_require_safe_git_ref` (routes/_specpath.py) returns the
 * value only after refusing a leading `-` and any character outside
 * `[\w./@+-]`; every caller passes it as one element of a list argv with no
 * shell, so it cannot pick the program or inject an option (#1428).
 */
class ArgvSanitizer extends CommandInjection::Sanitizer {
  ArgvSanitizer() {
    exists(DataFlow::CallCfgNode call, string name |
      name in ["_safe_arg", "_require_safe_git_ref"] and
      (
        call.getFunction().asExpr().(Name).getId() = name or
        call.getFunction().asExpr().(Attribute).getName() = name
      ) and
      this = call
    )
    or
    // The body of the validator IS the check: barrier its first parameter so
    // the regex inside the helper does not re-fire the alert it exists to clear.
    exists(Function f |
      f.getName() in ["_safe_arg", "_require_safe_git_ref"] and
      this.(DataFlow::ParameterNode).getParameter() = f.getArg(0)
    )
  }
}

from CommandInjectionFlow::PathNode source, CommandInjectionFlow::PathNode sink
where CommandInjectionFlow::flowPath(source, sink)
select sink.getNode(), source, sink, "This command line depends on a $@.", source.getNode(),
  "user-provided value"
