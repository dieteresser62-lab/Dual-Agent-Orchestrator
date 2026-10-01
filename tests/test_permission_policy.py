from pathlib import Path

import pytest

from permission_policy import GIT_WRITES, classify_implementer_denial, explain_implementer_denial


@pytest.fixture
def boundary(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "git-common"
    outside.mkdir()
    paths = tuple(root / name for name in (".git", ".orchestrator", "inbox", "outbox")) + (outside,)
    scratch = tmp_path / "scratch"
    scratch.mkdir(mode=0o700)
    return root, paths, scratch


@pytest.mark.parametrize('tool,data,rule,fragment', [
    ('Bash', {'command': 'git -C /fixture/repo checkout -- tests/x'}, 'git-write', 'checkout'),
    ('Bash', {'command': 'echo x > .git/hooks/x'}, 'protected-name', '.git'),
    ('Write', {'file_path': '.git/hooks/x'}, 'protected-path', '.git/hooks/x'),
    ('Bash', {'command': 'cp x /etc/passwd'}, 'outside-write', '/etc/passwd'),
    ('Read', {'file_path': '~/.ssh/id_rsa'}, 'credential-read', '~/.ssh'),
    ('Bash', {'command': 'echo $UNKNOWN; git commit -m "$UNKNOWN"'}, 'opaque:git-not-read-only', 'git commit'),
    ('Bash', {'command': 'echo "$(echo ok)" .git'}, 'substitution', '$(echo ok)'),
    ('Bash', {'command': 'cp *.js .gi[t]/hooks/'}, 'glob-protected', '.gi[t]'),
    ('Bash', {'command': r'echo escaped\ word; g\it commit'}, 'indirect-exec', r'g\it'),
    ('Bash', {'command': "cat <<'EOF'\nbody"}, 'heredoc-ambiguous', 'unterminated heredoc'),
    ('Bash', {'command': 'echo x > $UNKNOWN'}, 'opaque-unknown-target', '$UNKNOWN'),
    ('Bash', {}, 'invalid-input', 'invalid command'),
    ('Read', {'path': '\x00'}, 'inspection-error', 'null'),
    ('Write', {'file_path': 'docs/x'}, 'within-boundary', 'docs/x'),
    ('Read', {'file_path': '/usr/share/example'}, 'harmless-read', '/usr/share'),
    ('Bash', {'command': 'ls -la'}, 'read-only', 'ls'),
    ('Bash', {'command': 'echo "unterminated'}, 'opaque-harmless', 'unterminated'),
])
def test_denial_diagnostic_names_the_actual_rule(boundary, tool, data, rule, fragment):
    denial = {'tool_name': tool, 'tool_input': data}
    result = explain_implementer_denial(denial, *boundary)
    assert result.disposition == classify_implementer_denial(denial, *boundary)
    assert result.rule == rule
    assert fragment in result.fragment


def test_unbound_denial_has_a_reason(boundary):
    assert explain_implementer_denial({}, None, None).rule == 'unbound'


@pytest.mark.parametrize('command,expected', [
    ('node tests/run-single.mjs tests/balance-expenses-table-layout.test.mjs 2>&1 | tail -15; echo "exit=$?"; git diff --name-only; git status --porcelain -- app css tests', 'tolerated'),
    ('''S=$TMPDIR/repro.mjs && sed -n '229,380p' tests/balance-expenses-table-layout.test.mjs > $S && cat >> $S <<'EOF'
console.log(`n=${x}`, `backtick string`);
EOF
node $S''', 'tolerated'),
    ('for f in a b; do node t.mjs "$f" > "$TMPDIR/$f.log" 2>&1; done; echo "exit=$?"', 'tolerated'),
    ('rm -f "$TMPDIR/x"; echo $?', 'tolerated'),
    ('echo x > $HOME/.bashrc; echo $?', 'violation'),
    ('rm -rf "$D"/.git', 'violation'),
    ('cp x /etc/$F', 'violation'),
    ('f=.git; touch $f/x', 'violation'),
    ('cat ~/.ssh/id_rsa; echo $?', 'violation'),
    ('echo $? > .orchestrator/x', 'violation'),
    ('eval "$X"', 'violation'),
])
def test_round18_real_commands_and_counterexamples(boundary, command, expected):
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == expected


@pytest.mark.parametrize('parameter', ['?', '!', '$', '#', '-', '@', '*', *map(str, range(10))])
def test_special_parameters_do_not_make_shell_opaque(boundary, parameter):
    from permission_policy import _shell_words
    command = f'node t.mjs "${parameter}"; echo "exit=${parameter}"'
    assert _shell_words(command, boundary[2])
    result = explain_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary)
    assert result.disposition == 'tolerated'
    assert result.rule == 'within-boundary'


def test_function_positional_argument_rule_is_preserved(boundary):
    from permission_policy import _shell_words
    words = _shell_words('run() { node -e "$1"; }; node -e "$1"', boundary[2])
    assert '/function-argument' in words
    assert '$1' in words


@pytest.mark.parametrize('redirect', ['2>&1', '3>&2', '>&2', '4<&0', '>&-',
    '>/dev/null', '>>/dev/null', '&>/dev/null', '2>/dev/null',
    '>/dev/stdout', '>>/dev/stderr', '&>/dev/stdout', '2>/dev/stderr'])
@pytest.mark.parametrize('opaque', [False, True])
def test_channel_duplications_and_output_devices_are_not_writes(boundary, redirect, opaque):
    command = f'node t.mjs {redirect}; echo ' + ('"$UNBOUND"' if opaque else '"$?"')
    result = explain_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary)
    assert result.disposition == 'tolerated'
    assert result.rule != 'opaque-unknown-target'


@pytest.mark.parametrize('command', [
    'echo x > "$f"', 'node t.mjs >> $OUT/x', 'cp x "$OUT"', 'mv x "$OUT"',
    'ln -s x "$OUT"', 'tee "$OUT"', 'rm -f "$OUT/x"', 'touch "$OUT/x"',
    'mkdir "$OUT/x"', 'chmod 600 "$OUT/x"', 'rsync x "$OUT/x"', 'install x "$OUT/x"',
    'node t.mjs > "$TMPDIR/$f.log"',
    'echo x > "$f"; echo y > local',
])
def test_unknown_targets_are_tolerated_with_explicit_diagnostic(boundary, command):
    result = explain_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary)
    assert result.disposition == 'tolerated'
    assert result.rule == 'opaque-unknown-target'
    assert '$' in result.fragment


@pytest.mark.parametrize('command', [
    'node t.mjs "$unknown" > /etc/$F', 'cp x /etc/$F; echo "$unknown"',
    'echo x > "$unknown"; touch /etc/passwd',
    'echo x > "$unknown"; G=git; $G push',
    'X=eval; $X "$unknown"', 'echo x >& /etc/$F',
    'node t.mjs "$unknown" > ${HOME}/.bashrc',
    'node t.mjs "$unknown" > ~/.bashrc',
    'cd /etc\ntouch passwd',
    'while touch /etc/passwd; do true; done',
    'until touch /etc/passwd; do true; done',
])
def test_visible_violation_wins_over_unknown_targets(boundary, command):
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'violation'


@pytest.mark.parametrize('route', ['literal-parent-traversal', 'literal-link'])
def test_unknown_filename_does_not_hide_visible_outside_prefix(boundary, route):
    root, paths, scratch = boundary
    if route == 'literal-link':
        (scratch / 'link').symlink_to(paths[-1], target_is_directory=True)
        target = '$TMPDIR/link/$F'
    else:
        target = '$TMPDIR/../git-common/$F'
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': f'cp x "{target}"'}}, *boundary) == 'violation'


@pytest.mark.parametrize("tool,data,expected", [
    # The three smoke attempts: Write and two shell operations outside the repo.
    ("Write", {"file_path": "/tmp/claude-1000/mut/mutate.mjs", "content": "fake"}, "violation"),  # allowlist:provider -- profile configuration: real event or smoke path fixture
    ("Bash", {"command": 'mkdir -p "$TMPDIR/mut" && cp tests/check.mjs "$TMPDIR/mut/"'}, "tolerated"),
    ("Bash", {"command": 'node /tmp/claude-1000/mut/mutate.mjs'}, "tolerated"),  # allowlist:provider -- profile configuration: real event or smoke path fixture
    ("Bash", {"command": 'node "$TMPDIR/mut/mutate.mjs"'}, "tolerated"),
    ("Write", {"file_path": "src/normal.py"}, "tolerated"),
    ("Bash", {"command": "npm test"}, "tolerated"),
    ("Bash", {"command": "git status --short"}, "tolerated"),
    ("Bash", {"command": "git --no-pager diff"}, "tolerated"),
    ("Bash", {"command": "git branch --list"}, "tolerated"),
    ("Read", {"file_path": "/outside/normal.py"}, "tolerated"),
    ("Edit", {"file_path": "./.git/hooks/post-commit"}, "violation"),
    ("Write", {"file_path": "src/../.orchestrator/state.json"}, "violation"),
    ("NotebookEdit", {"notebook_path": "inbox/task.ipynb"}, "violation"),
    ("Bash", {"command": "cat .git/config"}, "tolerated"),
    ("Bash", {"command": "echo text >./.orchestrator/state.json"}, "violation"),
    ("Bash", {"command": "rm -f outbox/result.md"}, "violation"),
    ("Bash", {"command": "git branch -d feature"}, "violation"),
    ("Bash", {"command": "git branch -D feature"}, "violation"),
    ("Bash", {"command": "git branch -m feature"}, "violation"),
    ("Bash", {"command": "git branch -dm feature"}, "violation"),
    ("Bash", {"command": "git branch new-branch"}, "violation"),
    ("Bash", {"command": "git alias status"}, "violation"),
    ("Bash", {"command": 'eval "npm test"'}, "violation"),
    ("Bash", {"command": 'bash -c "npm test"'}, "violation"),
    ("Bash", {"command": "echo $UNKNOWN"}, "tolerated"),
    ("Bash", {"command": "echo $TMPDIR_SUFFIX"}, "tolerated"),
    ("Bash", {"command": 'echo "unterminated'}, "tolerated"),
    ("Bash", {}, "violation"),
    ("Write", {}, "violation"),
    ("Unknown", {}, "violation"),
])
def test_denial_classification_table(boundary, tool, data, expected):
    root, paths, scratch = boundary
    assert classify_implementer_denial({"tool_name": tool, "tool_input": data}, root, paths, scratch) == expected


@pytest.mark.parametrize("operation", sorted(GIT_WRITES))
def test_every_writing_git_operation_is_a_violation(boundary, operation):
    root, paths, scratch = boundary
    for command in (f"git {operation}", f"/usr/bin/git -C /tmp/another {operation}", f"npm test && git {operation}"):
        assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, root, paths, scratch) == "violation"


@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit", "Bash"])
def test_absolute_traversal_and_link_aliases_are_violations(boundary, tool):
    root, paths, scratch = boundary
    alias = root / "harmless"
    alias.symlink_to(paths[-1], target_is_directory=True)
    for path in (str(paths[-1] / "refs/main"), "harmless/refs/main", str(root / "src/../.git/config")):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        data = {"command": f"echo x > {path}"} if tool == "Bash" else {key: path}
        assert classify_implementer_denial({"tool_name": tool, "tool_input": data}, root, paths, scratch) == "violation"


@pytest.mark.parametrize("denial", [None, "bad", {}, {"tool_name": "Bash", "tool_input": "bad"}])
def test_unknown_denials_fail_closed(boundary, denial):
    assert classify_implementer_denial(denial, *boundary) == "violation"
    assert classify_implementer_denial(denial, None, None) == "violation"


# Complete fake commands reconstruct the diagnostic prefixes from the smoke
# log. The original stored excerpts end at 200 characters, so they cannot be
# passed off as executable or complete shell input.
@pytest.mark.parametrize("command", [
    '''M=$TMPDIR/mut && mkdir -p $M/tests && cp tests/simulator-tab-panel-pairing.test.mjs $M/tests/ &&
run() { node -e 'const fs=require("fs");const [src,dst,from,to]=process.argv.slice(1);const h=fs.readFileSync(src,"utf8");fs.writeFileSync(dst,h.replace(from,to));' "$1" "$M/$2" "$3" "$4"; }
run dashboard.html dashboard.html before after''',
    '''mkdir -p "$TMPDIR/mut" && cat > "$TMPDIR/mut/mutate.mjs" <<'EOF'
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const repo = '/tmp/fake-repo';
fs.copyFileSync(path.join(repo, 'dashboard.html'), '/tmp/smoke-temp/mut/dashboard.html');
EOF
node /tmp/smoke-temp/mut/mutate.mjs''',
    'M="$TMPDIR/mut" && mkdir -p "$M/tests" && cp tests/test.mjs "$M/tests/"',
    'SRC=tests/test.mjs; cp "$SRC" "$TMPDIR/test.mjs"',
    'M=$TMPDIR/mut; N=$M/sub; mkdir -p "$N"',
    'M=$TMPDIR/first; mkdir -p "$M"; M=$TMPDIR/second; mkdir -p "$M"',
])
def test_smoke_shell_assignments_functions_and_quoted_heredoc_are_tolerated(boundary, command):
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, *boundary) == "tolerated"


@pytest.mark.parametrize("redirect", ["<<'EOF'", '<<"EOF"', r"<<\EOF", "<<-'EOF'"])
def test_scratch_reproduction_with_inert_heredoc_substitutions(boundary, redirect):
    from shell_inspection import tokens
    # Reconstruction of the real follow-up: JS quotes, template strings and
    # backticks are data even when they would be invalid shell substitutions.
    body = '''console.log(collectProfileCellCssViolations('.profile-cell { --marker: "("; display: flex; }'));
const x = 1;
console.log(`n=${x}`, `backtick string`, '$(not shell)', '`unterminated', '<(not shell)');'''
    if redirect.startswith('<<-'):
        body = '\t' + body.replace('\n', '\n\t')
    command = (
        "S=$TMPDIR/repro.mjs && sed -n '229,380p' tests/balance-expenses-table-layout.test.mjs > $S "
        f"&& cat >> $S {redirect}\n{body}\n"
        + ('\tEOF\n' if redirect.startswith('<<-') else 'EOF\n') + 'node $S'
    )
    assert not any(item.substitution or item.subcommands for item in tokens(command))
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'tolerated'


@pytest.mark.parametrize('body', [
    '`rm -rf .orchestrator`', '$(rm -rf .orchestrator)',
    "'$(git push)'", '"`git push`"',
])
def test_unquoted_heredoc_substitutions_remain_active(boundary, body):
    from shell_inspection import tokens
    command = f'cat > "$TMPDIR/repro.mjs" <<EOF\n{body}\nEOF\nnode "$TMPDIR/repro.mjs"'
    assert any(item.substitution and item.subcommands for item in tokens(command))
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'violation'


@pytest.mark.parametrize('second,body,expected', [
    ("'TWO'", '`not shell` $(also not shell)', 'tolerated'),
    ('TWO', '`git push`', 'violation'),
])
def test_multiple_heredocs_in_one_command(boundary, second, body, expected):
    command = f'''cat > "$TMPDIR/repro.mjs" <<'ONE' <<{second}
console.log(`n=${{x}}`);
ONE
{body}
TWO
node "$TMPDIR/repro.mjs"'''
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == expected


@pytest.mark.parametrize('redirect,body', [
    ("<<'EOF'", '.orchestrator/state.json'),
    ('<<"EOF"', '.git/hooks/pre-commit'),
    (r'<<\EOF', 'inbox/task.md'),
    ("<<-'EOF'", '\toutbox/result.md'),
])
def test_protected_names_in_inert_heredocs_still_stop(boundary, redirect, body):
    command = f'cat > "$TMPDIR/repro.mjs" {redirect}\n{body}\nEOF'
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'violation'


@pytest.mark.parametrize('command', [
    "cat <<'EOF'\nbody", 'cat <<EOF\nbody', 'cat <<\nbody',
    "cat <<'EOF\nbody\nEOF", "cat <<'ONE' <<'TWO'\nbody\nONE",
    "cat <<'EOF'\nbody\n EOF", "cat <<-'EOF'\nbody\n EOF",
    'cat <<$(echo EOF)\nbody\nEOF', 'cat <<EOF\n$(unterminated\nEOF',
    'cat <<\\', 'cat <<${EOF',
])
def test_ambiguous_or_incomplete_heredocs_fail_closed(boundary, command):
    from shell_inspection import HeredocError, tokens
    with pytest.raises(HeredocError):
        tokens(command)
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'violation'


@pytest.mark.parametrize('command', [
    "echo '<<EOF `literal`'", 'echo "<<EOF"',
])
def test_quoted_heredoc_operator_text_is_not_a_redirect(boundary, command):
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'tolerated'


@pytest.mark.parametrize('following', ['git push', '$(echo g)it commit -am x'])
def test_commands_after_quoted_heredoc_are_still_inspected(boundary, following):
    command = f'cat > "$TMPDIR/repro.mjs" <<\'EOF\'\n`inert`\nEOF\n{following}'
    assert classify_implementer_denial({'tool_name': 'Bash', 'tool_input': {'command': command}}, *boundary) == 'violation'


@pytest.mark.parametrize("command", [
    "'git' 'add' src/file.py", 'true;git add src/file.py', 'printf x&&git push', "git 'commit'", 'G=git; $G push', 'A=g; B=it; $A$B reset',
    'M="$TMPDIR/../repo/.git"; touch "$M/index"', 'rm -rf .',
    "cat > /tmp/safe <<'EOF'\n.git/index\nEOF",
    # Opaque text stops when git or indirect execution is visible.
    'X=$UNKNOWN; git commit -m "$X"', 'echo "$(git push)"', 'N=$UNKNOWN; eval "$N"', 'A=$UNKNOWN; bash -c "$A"',
])
def test_quoted_git_writes_opaque_variables_and_control_heredocs_stop(boundary, command):
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, *boundary) == "violation"


@pytest.mark.parametrize("route", ["parent-traversal", "scratch-link"])
def test_tmpdir_expansion_uses_actual_bound_scratch_for_protection(boundary, route):
    root, paths, scratch = boundary
    (scratch / "innocent").symlink_to(paths[-1], target_is_directory=True)
    command = 'echo x > "$TMPDIR/../git-common/index"' if route == "parent-traversal" else 'echo x > "$TMPDIR/innocent/index"'
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, root, paths, scratch) == "violation"


# Smoke 2026-09-30 13:34: a mutation check in the private scratch, written as
# a function with node -e and escaped JavaScript, stopped the run although the
# sandbox blocks every protected write physically. Opaque but harmless text
# without git, indirect execution or protected names is tolerated.
@pytest.mark.parametrize("command", [
    'node "$TMPDIR/mutate.mjs"',
    'M=$UNBOUND/mut; mkdir -p "$M"', 'mkdir -p "$M"; M=$TMPDIR/mut',
    'M="$TMPDIR/mut"; M=$UNKNOWN; node "$M/mutate.mjs"',
    "cat > /tmp/safe <<EOF\n$UNKNOWN\nEOF", "cat > /tmp/safe <<'EOF'\nunterminated",
    '''M="$TMPDIR/mut" && rm -rf "$M" && mkdir -p "$M/tests" && cp tests/simulator-tab-panel-pairing.test.mjs "$M/tests/" && run() { name="$1"; shift; cp Simulator.html "$M/Simulator.html"; node -e "$1" "$M/Simulator.html"; (cd "$M" && node --test tests/) >/dev/null 2>&1 && echo "$name: survived" || echo "$name: killed"; }; run missing-panel "const fs=require(\\"fs\\");const p=process.argv[1];fs.writeFileSync(p,fs.readFileSync(p,\\"utf8\\").replace(/id=\\"tab-a\\"/,\\"id=\\\\\\"tab-x\\\\\\"\\"))"''',
])
def test_opaque_scratch_work_only_stops_visible_outside_targets(boundary, command):
    expected = "violation" if "cat >" in command else "tolerated"
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, *boundary) == expected
    root, paths, _ = boundary
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, root, paths) == expected


@pytest.mark.parametrize("arguments,expected", [
    ("", "tolerated"), ("--show-current", "tolerated"),
    ("--list 'feature/*' main", "tolerated"), ("--contains HEAD", "tolerated"),
    ("--merged", "tolerated"), ("--merged HEAD", "tolerated"),
    ("--no-merged HEAD -vv -a", "tolerated"), ("-v -r --format='%(refname)'", "tolerated"),
    ("--contains=HEAD --list 'feature/*'", "tolerated"),
    ("new-branch", "violation"), ("--show-current new-branch", "violation"),
    ("--contains HEAD new-branch", "violation"), ("--list -D feature", "violation"),
    ("--merged HEAD --force new-branch", "violation"), ("-l new-branch", "violation"),
    ("--edit-description", "violation"), ("--copy original new", "violation"),
    ("--list feature; git commit --allow-empty -m phase0", "violation"),
])
def test_read_only_branch_forms(boundary, arguments, expected):
    root, paths, scratch = boundary
    command = f'echo "TMPDIR=$TMPDIR"; git -C {root} branch {arguments}'
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, root, paths, scratch) == expected


@pytest.mark.parametrize("command,expected", [
    ('echo "TMPDIR=$TMPDIR"; cd {repo} && git status --short && git branch --show-current && ls -la . .git/hooks 2>&1 | head -50; ls -la .orchestrator inbox outbox 2>&1', "tolerated"),
    ('test -e "$HOME/.codex" && echo VISIBLE; echo "DAO_P0_ENV=${DAO_P0_ENV-<unset>}"; if [ -n "${ANTHROPIC_API_KEY+x}" ]; then echo PHASE0_API_KEY_VISIBLE; fi; echo "TMPDIR=$TMPDIR"; git -C {repo} branch --show-current', "violation"),  # allowlist:provider -- transport: credential-location access now stops
    ("ls .git > .git/hooks/x", "violation"), ("cat x | tee .orchestrator/y", "violation"),
    ("find .git -delete", "violation"), ('echo "$HOME"; git -C {repo} commit', "violation"),
    ("cd .git && touch x", "violation"), ("echo x >> inbox/a", "violation"),
    ('echo "$HOME"; git --no-pager -C {repo} --no-optional-locks status --short', "tolerated"),
    ('node "$UNKNOWN"; git -C {repo} branch --show-current', "tolerated"),
    ('node "$UNKNOWN"; git -C {repo} --no-pager push', "violation"),
    ('echo "$(git push)"', "violation"), ('echo "$(git status --short)"', "tolerated"),
    ('cat "$HOME/.git/config"', "violation"), ('ls ~/.orchestrator', "violation"),
    ('echo ">" .git', "tolerated"), ('ls .git >/dev/null 2>&1', "tolerated"),
    ('ls .git &>/dev/null', "tolerated"), ('cat .git/config\nls .orchestrator', "tolerated"),
    ('ls .git\ntouch inbox/a', "violation"), ('tree .git -o .git/hooks/x', "violation"),
    ('file -C .git/config', "violation"), ('git diff --output=.git/hooks/x', "violation"),
    ('rg --pre="touch .git/hooks/x" .git', "violation"), ('ls .git; eval "pwd"', "violation"),
    ('cat .git/config | sh -c "cat"', "violation"), ('ls .git; source ./setup', "violation"),
    (r'find .git \( -name "*.py" \)', "tolerated"),
    ("git --git-dir={repo}/.git branch -vv --format='%(refname)'", "tolerated"),
    (r'echo \> .git', "tolerated"), ('ls .git # comment\ngit push', "violation"),
    ('ls .git # > .git/hooks/x\npwd', "tolerated"),
    ('tree .git -o.git/hooks/x', "violation"), ('tree -ao .git/hooks/x', "violation"),
    ('echo ${VAR-<unset>}; ls .git', "tolerated"),
])
def test_s3_read_only_segments_and_boundary_counterexamples(boundary, command, expected):
    root, paths, scratch = boundary
    command = command.replace("{repo}", str(root))
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, root, paths, scratch) == expected


@pytest.mark.parametrize("name", ["ls", "cat", "head", "tail", "stat", "wc", "file", "du", "tree", "echo", "printf",
                                  "test -e", "[ -e", "pwd", "cd", "true", "realpath", "readlink", "grep pattern", "rg pattern",
                                  "find -L"])
def test_read_forms_may_name_protected_paths(boundary, name):
    command = name + " .git" + (" ]" if name.startswith("[") else "")
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, *boundary) == "tolerated"


@pytest.mark.parametrize("action", ["-exec touch x ;", "-execdir touch x ;", "-delete", "-ok touch x ;", "-okdir touch x ;",
                                    "-fprint .git/hooks/x", "-fprint0 .git/hooks/x", "-fprintf .git/hooks/x %p", "-fls .git/hooks/x"])
def test_find_write_and_execution_actions_are_not_read_forms(boundary, action):
    command = "find .git " + action
    assert classify_implementer_denial({"tool_name": "Bash", "tool_input": {"command": command}}, *boundary) == "violation"
