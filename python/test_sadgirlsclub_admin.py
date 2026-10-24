"""Safety and command-contract checks; no live domain is changed by these tests."""
import base64
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import sadgirlsclub_admin as admin

USER = "dn: CN=Alice,CN=Users,DC=example,DC=test\nsAMAccountName: alice\nobjectSid: S-1-5-21-1-2-3-1100\nuserAccountControl: 512\n"
GROUP = "dn: CN=Family,CN=Users,DC=example,DC=test\nsAMAccountName: Family\nobjectSid: S-1-5-21-1-2-3-1101\n"


class DirectoryTests(unittest.TestCase):
    def test_catalog_classifies_renamed_builtin_and_critical_groups_in_one_query(self):
        class Record(dict):
            def get(self, key, idx=0):
                return super().get(key)

        records = [Record(sAMAccountName=b"Renamed admins", objectSid=b"S-1-5-21-1-2-3-512"),
                   Record(sAMAccountName="Local operators", objectSid=b"S-1-5-32-544"),
                   Record(sAMAccountName="Family", objectSid=b"S-1-5-21-1-2-3-1101"),
                   Record(sAMAccountName="Critical", objectSid=b"S-1-5-21-1-2-3-1102", isCriticalSystemObject=b"TRUE")]
        db = MagicMock()
        db.search.return_value = records
        modules = {"ldb": SimpleNamespace(SCOPE_SUBTREE=2),
                   "samba.auth": SimpleNamespace(system_session=lambda: None),
                   "samba.dcerpc.security": SimpleNamespace(dom_sid=object),
                   "samba.ndr": SimpleNamespace(ndr_unpack=lambda kind, raw: raw.decode()),
                   "samba.param": SimpleNamespace(LoadParm=MagicMock()),
                   "samba.samdb": SimpleNamespace(SamDB=MagicMock(return_value=db))}
        with patch.dict(sys.modules, modules):
            result = admin.group_catalog()
        self.assertEqual({group["name"]: group["protected"] for group in result},
                         {"Renamed admins": True, "Local operators": True, "Family": False, "Critical": True})
        db.search.assert_called_once()
        self.assertEqual(db.search.call_args.kwargs["expression"], "(objectClass=group)")

    @patch.object(admin, "check_domain", return_value={"realm": "EXAMPLE.TEST"})
    @patch.object(admin, "group_catalog", return_value=[{"name": "Family", "protected": False},
                                                       {"name": "Renamed admins", "protected": True}])
    @patch.object(admin, "samba", return_value="alice\n")
    def test_overview_keeps_all_groups_and_supplies_filter_metadata(self, samba, catalog, domain):
        result = admin.dispatch({"action": "overview"})
        self.assertEqual(result["groups"], ["Family", "Renamed admins"])
        self.assertEqual(result["builtin_groups"], ["Renamed admins"])
        samba.assert_called_once_with("user", "list")

    def test_rejects_option_and_member_list_injection(self):
        for name in ["--configfile=/tmp/other", "alice,bob", "alice\nbob", "a|b", " name", "a\x00b"]:
            with self.subTest(name=name), self.assertRaises(admin.AdminError):
                admin.clean_name(name)

    def test_preserves_legitimate_group_spaces(self):
        self.assertEqual(admin.clean_name("Family Photos"), "Family Photos")

    def test_builtin_and_unknown_objects_are_protected(self):
        for sid in ["S-1-5-21-1-2-3-500", "S-1-5-21-1-2-3-512", "S-1-5-32-544", "", "unexpected"]:
            self.assertTrue(admin.protected({"objectsid": [sid]}))
        self.assertFalse(admin.protected(admin.attributes(USER)))
        self.assertTrue(admin.protected({"objectsid": ["S-1-5-21-1-2-3-1100"], "iscriticalsystemobject": ["TRUE"]}))

    def test_ldif_decodes_unicode_and_folding(self):
        value = base64.b64encode("Zoë Example".encode()).decode()
        attrs = admin.attributes(f"displayName:: {value[:5]}\n {value[5:]}\nmember: first\nmember: second\n")
        self.assertEqual(attrs["displayname"], ["Zoë Example"])
        self.assertEqual(attrs["member"], ["first", "second"])

    @patch.object(admin, "samba", return_value="")
    def test_create_password_is_stdin_only(self, samba):
        secret = "Neon Secret 53!"
        admin.dispatch({"action": "create_user", "name": "alice", "password": secret,
                        "password_confirm": secret, "must_change": True, "given_name": "Alice"})
        args, kwargs = samba.call_args
        self.assertEqual(args[:3], ("user", "add", "alice"))
        self.assertNotIn(secret, " ".join(args))
        self.assertIn("--must-change-at-next-login", args)
        self.assertEqual(kwargs["input_text"], secret + "\n" + secret + "\n")

    @patch.object(admin, "samba")
    def test_password_mismatch_and_newlines_never_execute(self, samba):
        for secret, confirm in [("one", "two"), ("one\ntwo", "one\ntwo"), ("", "")]:
            with self.assertRaises(admin.AdminError):
                admin.dispatch({"action": "create_user", "name": "alice", "password": secret, "password_confirm": confirm})
        samba.assert_not_called()

    @patch.object(admin, "samba", return_value=USER)
    def test_deletion_requires_exact_confirmation(self, samba):
        with self.assertRaises(admin.AdminError):
            admin.dispatch({"action": "delete_user", "name": "alice", "confirmation": "DELETE bob"})
        self.assertEqual(samba.call_count, 1)  # Only the read occurred.
        admin.dispatch({"action": "delete_user", "name": "alice", "confirmation": "DELETE alice"})
        self.assertEqual(samba.call_args.args, ("user", "delete", "alice"))

    @patch.object(admin, "samba", return_value=USER.replace("1100", "500"))
    def test_builtin_restriction_is_server_enforced(self, samba):
        with self.assertRaises(admin.AdminError):
            admin.dispatch({"action": "disable", "name": "alice"})
        self.assertEqual(samba.call_count, 1)

    @patch.object(admin, "samba", side_effect=[GROUP, USER, ""])
    def test_add_membership_uses_two_validated_objects(self, samba):
        admin.dispatch({"action": "add_member", "name": "Family", "member": "alice"})
        self.assertEqual(samba.call_args.args, ("group", "addmembers", "Family", "alice"))

    @patch.object(admin, "samba", return_value=GROUP.replace("1101", "512"))
    def test_critical_group_membership_is_protected(self, samba):
        with self.assertRaises(admin.AdminError):
            admin.dispatch({"action": "add_member", "name": "Domain Admins", "member": "alice"})
        self.assertEqual(samba.call_count, 1)

    @patch.object(admin.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "ok", ""))
    def test_process_has_no_shell_and_no_controlling_terminal(self, run):
        self.assertEqual(admin.samba("user", "list"), "ok")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/samba-tool", "user", "list"])
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["timeout"], 40)

    @patch.object(admin.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "Rejected TopSecret!"))
    def test_error_redacts_password(self, run):
        with self.assertRaises(admin.AdminError) as exc:
            admin.samba("user", "add", "alice", secret="TopSecret!")
        self.assertNotIn("TopSecret!", str(exc.exception))

    @patch.object(admin.subprocess, "run", side_effect=subprocess.TimeoutExpired("samba-tool", 40))
    def test_timeout_does_not_retry_a_write(self, run):
        with self.assertRaisesRegex(admin.AdminError, "Refresh"):
            admin.samba("user", "disable", "alice")
        self.assertEqual(run.call_count, 1)

    @patch.object(admin, "samba")
    def test_unknown_action_never_executes(self, samba):
        with self.assertRaises(admin.AdminError):
            admin.dispatch({"action": "domain provision", "name": "alice"})
        samba.assert_not_called()


if __name__ == "__main__":
    unittest.main()
