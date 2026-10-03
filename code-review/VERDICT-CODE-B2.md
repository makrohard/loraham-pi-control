9m 53s nachgedacht
Analysiert
Analysiert
Analysiert
Analysiert
Analysiert
Analysiert
Analysiert
Analysiert
ChatGPT hat gesagt:
commit	verdict (OK / FINDING)	what
1e648ac CR5-1	FINDING	The strict path correctly distinguishes absent from unreadable/malformed/top-level-wrong-schema indexes and all three writers use it. The supplied regression cases are genuine red-before/green-after, not preservation-only tests. 
gate1-CODE-B2
 
gate1-CODE-B2

↳ CR5-1 finding	FINDING	The “damaged inventory gives a typed refusal” guarantee is incomplete. _load_index(strict=True) only checks that the object is a dict, schema == 1, and certs is a list. It does not validate the list elements. Thus e.g. {"schema":1,"certs":[null]} passes the strict loader, after which issue_client_cert / revoke_client_cert execute e.get(...) and raise AttributeError, not the promised PKIError naming client-index.json and the recovery path. The regression test covers empty/truncated JSON, invalid JSON and schema 99, but not a structurally corrupt schema-1 inventory. 
gate1-CODE-B2
 
gate1-CODE-B2

af2f665 CR5-2	FINDING	The shell-vs-console branch itself is sensible: a shell restart is attempted only after reload leaves the console exposed; the console path declines the restart and names Apply. But the post-restart proof is incomplete. 
gate1-CODE-B2

↳ CR5-2 finding	FINDING	Successful restart re-probes only the console, not the stack proxies. remaining was obtained before the restart; after _ws.restart() succeeds the code updates only console_scope, then immediately computes proven = (not remaining) and (not console_exposed). If the old nginx configuration also had a remote stack proxy—the exact defect description says it could—remaining stays stale even though the restart removed it. The command can therefore report cessation unproven after a successful restart; worse, with console_exposed == False it supplies neither the Apply suffix nor next_commands. The regression fake contains only the :8443 console listener, so it cannot expose this defect. Re-probe the proxy state after a successful restart before computing proven. 
gate1-CODE-B2
 
gate1-CODE-B2

a151ec4 CR5-4	OK	PathContainmentError is converted at the four intended read boundaries: cert/key reads become PKIError, while index/pending reads retain their fail-safe behavior. The separate PEM ValueError path is unaffected. The server and client-CA symlink cases exercise the real escaping exception and were red before. 
gate1-CODE-B2

138c7d3 CR5-5	OK	On the stated second-write failure, the old key bytes are captured first, the failed cert write is converted to PKIError, and the key is atomically restored at mode 0600. The test injects the failure specifically on server.crt and additionally proves the restored key still matches the old certificate, so it is not merely testing an error message. The acknowledged power-loss window between the two renames remains outside this guarantee. 
gate1-CODE-B2
 
gate1-CODE-B2

c0bea75 CR4-1	OK	A helper-owned in-flight marker is excluded from the recovery decision, while foreign/interrupted markers and uninstall guards remain blockers. In helper context it deliberately verifies rather than attempts the unavailable in-process repair; verifier failure becomes a visible partial update. The test proves the original defect by requiring an actual verify-set invocation and proving repair was not called. 
gate1-CODE-B2
 
gate1-CODE-B2

359989e CR4-2	OK	The annotated tag is first resolved through refs/tags/<tag>^{tag}^{commit} and must resolve exactly to the pin before verify-tag is used; otherwise verification falls back to the pin commit. That closes the unrelated-signed-tag hole and handles describe strings/lightweight tags as intended. The three-way test distinguishes both verification verbs and models the describe-resolution trap called out by the implementation report. 
gate1-CODE-B2
 
gate1-CODE-B2

9b93b3b CR4-5	OK	Within the supplied specification, the static search covers the newly required user-control/runtime/generator/system/XDG locations, exact-unit, prefix-wide and type-wide drop-ins, plus higher-priority shadow fragments. The seven regression cases each start from an asserted canonical state, then add one override and turn the result to overridden; this is a meaningful red-before test rather than a fake status stub. The packet correctly still calls for the Pi live check for stock-drop-in false positives. 
gate1-CODE-B2
 
gate1-CODE-B2

f514605 CR4-7	OK	Filtering now uses lexists() to distinguish genuinely absent receipt leaves from present leaves that the hash layer represents as actual == "". This protects symlinks/non-regular/unhashable leaves while retaining the existing truly-gone behavior and force path. Both the symlink and bounded-size surrogate are real regressions: on the parent they retire/delete, after the commit they refuse and preserve the leaf. 
gate1-CODE-B2
 
gate1-CODE-B2

RED
