"""Validate the recorded API snapshot; these tests do not query live GitHub."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class GitHubSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / 'docs/github-settings.json').read_text(encoding='utf-8'))

    def test_repository_and_security_settings(self):
        repo = self.data['repository']
        self.assertEqual('public', repo['visibility'])
        self.assertEqual('master', repo['default_branch'])
        self.assertFalse(repo['allow_merge_commit'])
        for key in ['allow_squash_merge', 'allow_rebase_merge', 'allow_auto_merge',
                    'allow_update_branch', 'delete_branch_on_merge']:
            self.assertTrue(repo[key], key)
        for key in ['secret_scanning', 'secret_scanning_push_protection', 'dependabot_security_updates']:
            self.assertEqual('enabled', repo['security_and_analysis'][key]['status'])
        self.assertTrue(self.data['private_vulnerability_reporting']['enabled'])
        self.assertTrue(self.data['immutable_releases']['enabled'])

    def test_actions_and_codeql(self):
        self.assertTrue(self.data['actions']['permissions']['sha_pinning_required'])
        self.assertEqual({'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False},
                         self.data['actions']['workflow_permissions'])
        self.assertEqual('configured', self.data['codeql']['state'])
        self.assertEqual(['actions', 'python'], self.data['codeql']['languages'])

    def test_single_codeowner_approval_and_strict_trusted_check(self):
        branch = next(r for r in self.data['rulesets'] if r['target'] == 'branch')
        self.assertEqual('active', branch['enforcement'])
        self.assertEqual([], branch['bypass_actors'])
        self.assertEqual({'ref_name': {'include': ['~DEFAULT_BRANCH'], 'exclude': []}}, branch['conditions'])
        rules = {r['type']: r.get('parameters', {}) for r in branch['rules']}
        self.assertTrue({'deletion', 'non_fast_forward', 'copilot_code_review'} <= set(rules))
        review = rules['pull_request']
        self.assertEqual(1, review['required_approving_review_count'])
        for key in ['require_code_owner_review', 'dismiss_stale_reviews_on_push',
                    'require_last_push_approval', 'required_review_thread_resolution']:
            self.assertTrue(review[key], key)
        checks = rules['required_status_checks']
        self.assertTrue(checks['strict_required_status_checks_policy'])
        self.assertEqual([{'context': 'CI required', 'integration_id': 15368}], checks['required_status_checks'])
        # External-fork analysis is a prerequisite; advisory mode must not silently become required.
        self.assertNotIn('code_scanning', rules)

    def test_version_tags_cannot_move_or_be_deleted(self):
        tags = next(r for r in self.data['rulesets'] if r['target'] == 'tag')
        self.assertEqual('active', tags['enforcement'])
        self.assertEqual([], tags['bypass_actors'])
        self.assertEqual({'ref_name': {'include': ['refs/tags/v*'], 'exclude': []}}, tags['conditions'])
        self.assertEqual({'deletion', 'non_fast_forward'}, {r['type'] for r in tags['rules']})

    def test_environment_approval_and_ref_policies(self):
        envs = {e['name']: e for e in self.data['environments']}
        self.assertEqual({'release', 'pypi-importer', 'pypi-compiler', 'maven-central'}, set(envs))
        for name, env in envs.items():
            self.assertEqual({'protected_branches': False, 'custom_branch_policies': True},
                             env['deployment_branch_policy'])
            self.assertEqual(0, env['wait_timer'])
            expected = [{'name': 'v*', 'type': 'tag'}]
            if name == 'release':
                expected.insert(0, {'name': 'master', 'type': 'branch'})
                self.assertFalse(env['can_admins_bypass'])
                self.assertFalse(env['prevent_self_review'])
                self.assertEqual([{'type': 'User', 'id': 31504595}, {'type': 'User', 'id': 308664957}],
                                 env['reviewers'])
            else:
                self.assertEqual([], env['reviewers'])
            self.assertEqual(expected, env['deployment_policies'])

    def test_no_volatile_resource_metadata(self):
        def visit(value, path=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertNotIn(key, {'node_id', 'created_at', 'updated_at', 'html_url'})
                    self.assertFalse(key.endswith('_url'), key)
                    if key == 'id':
                        self.assertIn('reviewers', path)
                    visit(item, path + (key,))
            elif isinstance(value, list):
                for item in value:
                    visit(item, path)
        visit(self.data)


if __name__ == '__main__':
    unittest.main()
