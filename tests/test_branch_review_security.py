"""Provider-free regressions for the branch-review security findings."""
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

import agent_runtime
from agent_adapters import AgentOutputError, AgentPermissionError
from permission_policy import classify_implementer_denial
from toolchain_paths import CREDENTIAL_LOCATIONS
from model_catalog import hardened_reviewer_catalog
from native_provider_schema import NativeProviderSchemaError, normalize_transport_profile
from native_implementer_contract import NativeImplementerErrorCode
from test_claude_implementer_adapter import _prepared, _stream # allowlist:provider -- transport: fake implementer fixture
from test_codex_review_adapter import _prepared as prepared_reviewer # allowlist:provider -- transport: fake reviewer fixture
from test_provider_identity import _executable, _fake_runner


@pytest.fixture
def boundary(tmp_path):
    repo, scratch = tmp_path / 'repo', tmp_path / 'scratch'
    repo.mkdir(); scratch.mkdir()
    return repo, tuple(repo / name for name in ('.git', '.orchestrator', 'inbox', 'outbox')), scratch


@pytest.mark.parametrize('command', [
    '''echo "'" `cp /tmp/x .git/hooks/pre-commit` "'"''',
    '''ls "'" `rm -rf .orchestrator` "'"''',
    '''echo "'" `git push` "'"''',
    '''echo "'" "$(cp /tmp/x .git/hooks/pre-commit)" "'"''',
    'cp /tmp/x .gi[t]/hooks/', "echo '[core]' >> .gi?/config",
    'cp x .orchestrato*/state.json', 'ln -s .gi?/hooks h; cp /tmp/x h/pre-commit',
    r"cp /tmp/x $'\x2egit/hooks/pre-commit'",
    r'''cp /tmp/x "$(printf '\x2egit')/hooks/pre-commit"''',
    r'g\it commit -am x', "$'git' reset --hard", '$(echo g)it commit -am x',
    'cd "$(git rev-parse --git-dir)" && cp /tmp/x hooks/pre-commit',
    "dash -c 'touch x'", 'cat <(touch .git/hooks/x)', 'echo x > >(cat > inbox/a)',
    'echo x > ~/.bashrc', 'cp x /etc/passwd', 'touch ~/.codex/x',  # allowlist:provider -- profile configuration: credential-location regression
    'echo x > .claude/settings.json', 'cd /etc && touch passwd',  # allowlist:provider -- profile configuration: protected agent settings
    'cd $UNKNOWN && touch x', 'TMPDIR=/etc; touch "$TMPDIR/passwd"',
])
def test_obfuscated_or_external_actions_stop(boundary, command):
    assert classify_implementer_denial({'tool_name':'Bash','tool_input':{'command':command}}, *boundary) == 'violation'


@pytest.mark.parametrize('command', [
    "echo '`git push`'", "echo '$(git push)'", 'echo "`git status --short`"',
    'cat /usr/share/doc/example', 'python3 -c "print(1)"', 'node -e "console.log(1)"',
    'perl -e "print 1"', 'cd docs && touch file', 'cp /usr/share/example local',
    'echo x >/dev/null', 'M=$TMPDIR/mut; mkdir -p "$M"; touch "$M/a"',
])
def test_inert_quotes_library_reads_and_bound_writes_remain_tolerated(boundary, command):
    assert classify_implementer_denial({'tool_name':'Bash','tool_input':{'command':command}}, *boundary) == 'tolerated'


@pytest.mark.parametrize('tool', ['Read','Grep','Glob','Write','Edit','NotebookEdit','Bash'])
@pytest.mark.parametrize('location', (*CREDENTIAL_LOCATIONS, '/proc/self/environ', '/outside/.env.local'))
def test_credential_locations_are_shared_and_never_read(boundary, monkeypatch, tmp_path, tool, location):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    path = str(Path.home() / location)
    data = {'command':f'cat {path}'} if tool == 'Bash' else {'file_path':path,'path':path,'notebook_path':path}
    assert classify_implementer_denial({'tool_name':tool,'tool_input':data}, *boundary) == 'violation'


@pytest.mark.parametrize('tool', ['Write','Edit','NotebookEdit'])
@pytest.mark.parametrize('path', ['.claude/settings.json', 'docs/*.txt', '/etc/passwd', 'link/new.txt'])  # allowlist:provider -- profile configuration: denied agent configuration path
def test_file_tools_reject_links_globs_and_external_targets(boundary, tool, path):
    (boundary[0] / 'link').symlink_to(boundary[2], target_is_directory=True)
    data = {'notebook_path':path} if tool == 'NotebookEdit' else {'file_path':path}
    assert classify_implementer_denial({'tool_name':tool,'tool_input':data}, *boundary) == 'violation'


@pytest.mark.parametrize('field', ['denyWrite','deny'])
def test_normalizer_requires_exact_bound_protection(tmp_path, field):
    root, adapter, bundle, prepared = _prepared(tmp_path)
    try:
        settings = json.loads(prepared.command[prepared.command.index('--settings')+1])
        if field == 'denyWrite':
            settings['sandbox']['filesystem'][field][0] = str(root / 'wrong-boundary')
        else:
            settings['permissions'][field][:2] = ['Edit(./wrong-boundary)','Edit(./wrong-boundary/**)']
        encoded = json.dumps(settings, sort_keys=True, separators=(',',':'))
        command = list(prepared.command)
        command[command.index('--settings')+1] = encoded
        with pytest.raises(NativeProviderSchemaError, match='protection paths differ'):
            normalize_transport_profile(bundle.capability_profile, command,
                bound_settings_json=encoded, bound_environment=adapter.env,
                bound_scratch=adapter._scratch, bound_repository_root=root,
                bound_protected_paths=adapter._protected_paths)
    finally:
        adapter.cleanup()


@pytest.mark.parametrize('path', ['.git/HEAD','.git/config','.git/index','.git/hooks/x',
    '.git/info/exclude','.git/refs/heads/x','.orchestrator/state.json',
    '.orchestrator/artifacts/run/records/new.json','inbox/x','outbox/x'])
@pytest.mark.parametrize('change', ['content','mode','symlink','delete'])
def test_every_protected_tree_detects_changes(tmp_path, path, change):
    root, adapter, _, _ = _prepared(tmp_path)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.write_text('original')
    adapter.before_provider_process()
    try:
        if change == 'content': target.write_text('tampered')
        elif change == 'mode': target.chmod(target.stat().st_mode ^ 0o100)
        elif change == 'symlink':
            target.unlink(); target.symlink_to(root / 'unprotected')
        else: target.unlink()
        with pytest.raises(AgentPermissionError, match='changed protected trees'):
            adapter.after_provider_process()
        assert str(target) in adapter.metadata['protected_tree_changes']
    finally:
        adapter.cleanup()


def test_only_parent_owned_process_metadata_and_open_log_are_excluded(tmp_path):
    root, adapter, _, _ = _prepared(tmp_path)
    process_file = root / '.orchestrator/attempt.process.json'
    process_file.parent.mkdir(parents=True, exist_ok=True)
    process_file.write_text('before')
    log = root / '.orchestrator/stream.log'
    handler = logging.FileHandler(log)
    logging.getLogger().addHandler(handler)
    adapter.bind_process_evidence(process_file)
    try:
        adapter.before_provider_process()
        process_file.write_text('after')
        handler.emit(logging.LogRecord('fake',logging.INFO,'',0,'stream',(),None))
        adapter.after_provider_process()
        adapter.before_provider_process()
        (root / '.orchestrator/records.json').write_text('not parent-owned')
        with pytest.raises(AgentPermissionError): adapter.after_provider_process()
    finally:
        logging.getLogger().removeHandler(handler); handler.close(); adapter.cleanup()


def test_runtime_checks_protected_trees_even_after_invalid_process_output(tmp_path, monkeypatch):
    root, adapter, bundle, _ = _prepared(tmp_path)
    monkeypatch.setattr(agent_runtime, 'verify_agent_capabilities', lambda *a, **k:None)
    monkeypatch.setattr(agent_runtime, '_bound_launch_command', lambda adapter, cmd:list(cmd))
    def fake_process(*args, **kwargs):
        (root / '.git/hooks/new').write_text('escape')
        return subprocess.CompletedProcess(args[1], 0, 'invalid output', '')
    monkeypatch.setattr(agent_runtime,'_run_agent_process',fake_process)
    from agent_runtime import run_native_implementer_agent_checked, AgentInvocationError, AgentFailureKind, OrchestratorConfig
    from provider_input_budget import default_provider_input_budget_policy
    from agent_roles import AgentSlot
    occupancy = tuple((slot.value, "implementer" if slot.value == "implementer" else "reviewer", adapter.name if slot.value == "implementer" else "codex") for slot in AgentSlot) # allowlist:provider -- profile configuration: tested occupancy
    with pytest.raises(AgentInvocationError) as caught:
        run_native_implementer_agent_checked(adapter=adapter,bundle=bundle,raw_response_path=tmp_path/"response.json",
            write_file=lambda path,text:path.write_text(text),pre_start_callback=None,provider_attempt_lifecycle=None,config=OrchestratorConfig(repo_root=root, inbox_dir=root / "inbox", outbox_dir=root / "outbox", provider_input_budget=default_provider_input_budget_policy(occupancy)),
            shorten=lambda text, limit:text or '',operation='implementer_plan',binding_fingerprint='a'*64)
    assert caught.value.kind == AgentFailureKind.PERMISSION
    assert 'protected_tree_changes' in adapter.metadata


@pytest.mark.parametrize('field,value,code', [
    ('request_id','wrong',NativeImplementerErrorCode.REQUEST_MISMATCH),
    ('schema_version','wrong',NativeImplementerErrorCode.SCHEMA_INVALID),
])
def test_bound_implementer_response_fields_are_contract_errors(tmp_path, field, value, code):
    root, adapter, bundle, _ = _prepared(tmp_path)
    result = {'request_id':bundle.bound_context.request_id,
              'schema_version':adapter.role_binding.contract, field:value}
    try:
        with pytest.raises(AgentOutputError) as caught:
            adapter.extract_output(_stream({'is_error':False,'structured_output':{'result':result}}),'',{})
        failure = agent_runtime.classify_agent_failure(adapter.name,caught.value,invocation_id="fake-contract",
            session_limit_profile=adapter.session_limit_profile)
        assert failure.kind == agent_runtime.AgentFailureKind.OUTPUT
        assert failure.native_implementer_rejection == code
        assert failure.native_implementer_response_retryable
        from workflow_failure_recording import _native_retry_budget
        assert _native_retry_budget(diagnostic_code='NATIVE-IMPLEMENTER-FORM',
            prior_transport_failures=0,prior_contract_rejections=0,
            max_transport_failures=3,max_contract_rejections=3) == (0,1,'max_contract_rejections',True)
    finally:
        adapter.cleanup()


def test_result_text_session_limit_is_classified_as_quota(tmp_path):
    _, adapter, _, _ = _prepared(tmp_path)
    try:
        with pytest.raises(AgentOutputError) as caught:
            adapter.extract_output(_stream({'is_error':True,'result':"You've hit your session limit"}),'',{})
        caught.value.exit_code = 1
        failure = agent_runtime.classify_agent_failure(adapter.name,caught.value,invocation_id="fake-session",
            session_limit_profile=adapter.session_limit_profile)
        assert failure.kind == agent_runtime.AgentFailureKind.QUOTA
        assert 'session limit' in failure.technical_text
    finally:
        adapter.cleanup()


@pytest.mark.parametrize('field', ['supports_mcp_tools','browser_enabled','new_agent_mode','extra_search'])
def test_unknown_tool_capabilities_fail_closed(field):
    with pytest.raises(ValueError, match='unclassified reviewer tool capability'):
        hardened_reviewer_catalog({'models':[{'slug':'fake',field:True}]}, 'fake')


def test_known_shell_formats_and_instruction_metadata_remain_allowed():
    row = {'slug':'fake','description':'updated','tool_mode':'code_mode_only','shell_type':'shell_command',
           'apply_patch_tool_type':'freeform','include_shell_usage_instructions':True,
           'include_node_repl_usage_instructions':False,'node_repl_tool_type':None,
           'experimental_supported_tools':['tool'],'supports_search_tool':True,'multi_agent_version':'v2'}
    hardened = json.loads(hardened_reviewer_catalog({'models':[row]}, 'fake'))['models'][0]
    assert hardened['experimental_supported_tools'] == [] and hardened['supports_search_tool'] is False
    assert 'multi_agent_version' not in hardened
    assert hardened['shell_type'] == row['shell_type'] and hardened['tool_mode'] == row['tool_mode']


def test_reviewer_environment_filters_parent_tokens(tmp_path, monkeypatch):
    for key in ('GH_TOKEN','ANTHROPIC_API_KEY','AWS_SECRET_ACCESS_KEY','DAO_DECOY_TOKEN'):
        monkeypatch.setenv(key,'must-not-pass')
    monkeypatch.setenv('LC_ALL','C'); monkeypatch.setenv('CODEX_HOME','/decoy/home') # allowlist:provider -- profile configuration: native login location
    adapter, repo, bundle = prepared_reviewer(tmp_path)
    boundary = adapter.review_execution_boundary(repo, None)
    boundary.__enter__()
    adapter.prepare_native_provider_input(bundle)
    try:
        assert adapter.inherit_process_environment is False
        assert adapter.env['LC_ALL'] == 'C'
        assert adapter.env['CODEX_HOME'] == '/decoy/home' # allowlist:provider -- profile configuration: native login location
        assert not {'GH_TOKEN','ANTHROPIC_API_KEY','AWS_SECRET_ACCESS_KEY','DAO_DECOY_TOKEN'} & adapter.env.keys()
        from agent_config import isolation_options_digest
        assert isolation_options_digest(adapter.settings)
        assert 'HOME' in adapter.env and 'PATH' in adapter.env
    finally:
        boundary.__exit__(None,None,None)
        adapter.cleanup()


def test_npm_native_binary_hash_is_bound_and_rechecked_without_execution(tmp_path, monkeypatch):
    from provider_identity import capture_provider_identity, ProviderIdentity
    entry = _executable(tmp_path / 'node_modules/@openai/codex/bin/codex.js', b'#!/usr/bin/env node\n') # allowlist:provider -- profile configuration: fake npm package
    native = _executable(entry.parent.parent / 'vendor/linux/bin/codex', b'fake native') # allowlist:provider -- profile configuration: fake native executable
    node = _executable(tmp_path / 'bin/node', b'fake node')
    monkeypatch.setenv('PATH',str(node.parent))
    bound = capture_provider_identity(str(entry),('--version',),_fake_runner({str(entry):'fake-cli 1',str(node):'v22.23.2'}))
    import hashlib
    assert bound.native_binary_sha256 == hashlib.sha256(native.read_bytes()).hexdigest()
    assert ProviderIdentity.from_dict(bound.to_dict()).digest == bound.digest
    native.write_text('changed native')
    calls=[]
    with pytest.raises(ValueError, match='identity differs'):
        capture_provider_identity(str(entry),('--version',),lambda cmd:(calls.append(cmd) or (0,'fake-cli 1','')),expected=bound)
    assert calls == []
    from test_provider_identity import FakeAdapter
    adapter = FakeAdapter()
    adapter.provider_identity = bound
    adapter.cli_binary = str(entry)
    monkeypatch.setattr(agent_runtime,'run_local_command',lambda *a,**kw:pytest.fail('drifted native binary was executed'))
    with pytest.raises(agent_runtime.AgentCompatibilityError, match='binary identity drift'):
        agent_runtime._check_bound_provider_identity(adapter)


@pytest.mark.parametrize('command', ['cp /tmp/x /etc/passwd >/dev/null',
    'cp /tmp/x /etc/passwd 2>&1', 'cp /tmp/x --target-directory=/etc',
    'mv /outside/x local', 'tee /outside/new', 'install x /outside/new',
    'rm /outside/new', 'mkdir /outside/new', 'chmod 600 /outside/new', 'rsync x /outside/new'])
def test_every_explicit_shell_write_target_is_checked(boundary, command):
    assert classify_implementer_denial({'tool_name':'Bash','tool_input':{'command':command}}, *boundary) == 'violation'


def test_allowlist_is_bound_in_reviewer_isolation_identity(tmp_path, monkeypatch):
    import agent_config
    adapter, _, _ = prepared_reviewer(tmp_path)
    before = agent_config.isolation_options_digest(adapter.settings)
    monkeypatch.setattr(agent_config,'REVIEWER_ENVIRONMENT_POLICY',(*agent_config.REVIEWER_ENVIRONMENT_POLICY,'EXTRA'))
    assert agent_config.isolation_options_digest(adapter.settings) != before


@pytest.mark.parametrize('tool,data', [('Glob',{'path':'~','pattern':'.co?ex/*'}),
    ('Glob',{'pattern':'~/.ssh/*'}),('Grep',{'path':'/outside','glob':'.env*','pattern':'TOKEN'})])
def test_file_searches_check_the_effective_path_and_glob(boundary, tool, data):
    assert classify_implementer_denial({'tool_name':tool,'tool_input':data}, *boundary) == 'violation'


def test_record_schema_roundtrips_native_hash_and_preserves_old_identity_shape(tmp_path):
    from artifact_models import ArtifactRecord, Role, artifact_payload_document
    from test_artifact_models import _record, _attempt_for_pair
    from dataclasses import replace
    payload = _attempt_for_pair('codex',Role.IMPLEMENTER) # allowlist:provider -- profile configuration: identity record fixture
    old = artifact_payload_document(payload)
    assert 'native_binary_sha256' not in old['binary_identity']
    identity = replace(payload.binary_identity,native_binary_path=str(tmp_path/'native'),native_binary_sha256='a'*64)
    changed = replace(payload,binary_identity=identity)
    record = _record(changed)
    assert ArtifactRecord.from_dict(record.to_dict()) == record
    assert record.to_dict()['payload']['binary_identity']['native_binary_sha256'] == 'a'*64


def test_catalog_binds_operator_violation_expectations():
    from scripts.qualification.phase0_catalog import IMPLEMENTER_CASES
    assert not IMPLEMENTER_CASES['W1'].violation_expected
    assert all(IMPLEMENTER_CASES[f'W{i}'].violation_expected for i in range(2,9))


def test_offline_external_denials_now_expect_violation(tmp_path, monkeypatch):
    from scripts.qualification import offline_boundary as boundary
    from scripts.qualification.profiles import BOUNDARY_IMPLEMENTER
    from test_offline_boundary import identity
    paths = boundary.fixture(tmp_path,implementer=True)
    bound = identity(tmp_path / 'fake-binary',BOUNDARY_IMPLEMENTER)
    with boundary.adapter_invocation(BOUNDARY_IMPLEMENTER,paths['repo'],bound,'offline fixture') as inv:
        _, expectations, _ = boundary.scripts_for(BOUNDARY_IMPLEMENTER,inv,paths)
        labels = {item['label']:item for item in expectations}
        for label in ('absolute','traversal','file-symlink','directory-symlink','tmp','toolchain'):
            assert labels[label+'-file-denied']['disposition'] == 'violation'
        assert labels['home-hidden']['disposition'] == 'violation'


@pytest.mark.parametrize('command,expected', [
    ('''echo "'" `cp /tmp/x /outside/new` "'"''','violation'),
    ('''echo "'" "$(cp /tmp/x /outside/new)" "'"''','violation'),
    ('cd /etc; echo "$(touch passwd)"','violation'),
    ('echo "$(touch local)"','tolerated'),
    ('echo "$(echo $(git push))"','violation'),
    ("echo '$(touch /outside/new)'",'tolerated'),
])
def test_substitutions_are_inspected_in_the_inherited_working_directory(boundary, command, expected):
    assert classify_implementer_denial({'tool_name':'Bash','tool_input':{'command':command}}, *boundary) == expected
