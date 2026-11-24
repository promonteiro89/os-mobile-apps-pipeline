import datetime
import http.client
import io
import json
import os
import plistlib
import socket
import sys
import tempfile
import urllib.error
import urllib.request
import unittest
from unittest import mock
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lifetime  # noqa: E402


def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(number, payload):
    payload = payload.encode() if isinstance(payload, str) else payload
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def attribute(name, value, ns=""):
    # aapt2 XmlAttribute: namespace_uri=1, name=2, value=3
    return field(1, ns) + field(2, name) + field(3, value)


def proto_manifest(package, version_code, version_name):
    android = "http://schemas.android.com/apk/res/android"
    element = (
        field(1, field(1, "android") + field(2, android))  # namespace declaration
        + field(3, "")
        + field(4, "manifest")
        + field(5, attribute("versionCode", version_code, android))
        + field(5, attribute("versionName", version_name, android))
        + field(5, attribute("package", package))
    )
    return field(1, element)  # XmlNode.element


def mobileprovision(**profile):
    # Real profiles are a CMS envelope around an XML plist; the envelope bytes are irrelevant here.
    return b"\x30\x82\x01\x00junk" + plistlib.dumps(profile) + b"\x00trailer"


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_ipa(self, profile):
        path = self.dir / "app.ipa"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr(
                "Payload/App.app/Info.plist",
                plistlib.dumps(
                    {"CFBundleIdentifier": "com.example.app", "CFBundleShortVersionString": "3.2.1", "CFBundleVersion": "321"},
                    fmt=plistlib.FMT_BINARY,
                ),
            )
            if profile is not None:
                z.writestr("Payload/App.app/embedded.mobileprovision", mobileprovision(**profile))
        return path

    def test_aab_manifest(self):
        path = self.dir / "app.aab"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("base/manifest/AndroidManifest.xml", proto_manifest("com.example.app", "30201", "3.2.1"))
        self.assertEqual(
            lifetime.inspect_binary(path),
            {"identifier": "com.example.app", "versionName": "3.2.1", "versionCode": "30201"},
        )

    def test_ipa_app_store(self):
        meta = lifetime.inspect_binary(self.make_ipa({"Name": "Dist", "TeamIdentifier": ["ABCDE12345"], "Entitlements": {}}))
        self.assertEqual(meta["identifier"], "com.example.app")
        self.assertEqual((meta["versionName"], meta["versionCode"]), ("3.2.1", "321"))
        self.assertEqual(meta["profileType"], "app-store")
        self.assertEqual(meta["teamId"], "ABCDE12345")

    def test_ipa_profile_types(self):
        cases = {
            "ad-hoc": {"ProvisionedDevices": ["x"], "Entitlements": {"get-task-allow": False}},
            "development": {"ProvisionedDevices": ["x"], "Entitlements": {"get-task-allow": True}},
            "enterprise": {"ProvisionsAllDevices": True},
        }
        for expected, profile in cases.items():
            with self.subTest(expected):
                self.assertEqual(lifetime.inspect_binary(self.make_ipa(profile))["profileType"], expected)
        self.assertEqual(lifetime.inspect_binary(self.make_ipa(None))["profileType"], "unsigned")

    def test_development_profile_without_devices_is_not_app_store(self):
        for devices in ({"ProvisionedDevices": []}, {}):
            profile = dict(devices, Entitlements={"get-task-allow": True})
            with self.subTest(profile):
                self.assertEqual(lifetime.inspect_binary(self.make_ipa(profile))["profileType"], "development")

    def test_profile_expiry(self):
        past, future = datetime.datetime(2020, 1, 1), datetime.datetime(2999, 1, 1)
        self.assertTrue(lifetime.inspect_binary(self.make_ipa({"ExpirationDate": past}))["profileExpired"])
        self.assertFalse(lifetime.inspect_binary(self.make_ipa({"ExpirationDate": future}))["profileExpired"])
        self.assertFalse(lifetime.profile_expired({}))
        self.assertTrue(lifetime.profile_expired({"ExpirationDate": datetime.datetime(2025, 6, 1)}, now=datetime.datetime(2025, 6, 2)))

    def test_malformed_ipa_is_a_pipeline_error(self):
        path = self.dir / "bad.ipa"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Payload/App.app/Info.plist", b"not a plist")
        with self.assertRaises(lifetime.PipelineError):
            lifetime.inspect_binary(path)

    def test_malformed_provisioning_profile_is_a_pipeline_error(self):
        path = self.make_ipa(None)
        with zipfile.ZipFile(path, "a") as z:
            z.writestr("Payload/App.app/embedded.mobileprovision", b"junk<?xml version='1.0'?><plist><dict><key>x</broken></plist>")
        with self.assertRaises(lifetime.PipelineError):
            lifetime.inspect_binary(path)

    def test_aab_without_manifest_is_a_pipeline_error(self):
        path = self.dir / "bad.aab"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("base/dex/classes.dex", b"")
        with self.assertRaises(lifetime.PipelineError):
            lifetime.inspect_binary(path)


APP = {
    "id": "my-app",
    "name": "My App",
    "lifetimeAppId": "00000000-0000-4000-8000-000000000000",
    "androidPackage": "com.example.app",
    "iosBundleId": "com.example.app",
    "environment": "Production",
    "platforms": ["android", "ios"],
}


def run_cli(*argv):
    """Run the CLI and return the GITHUB_OUTPUT key/values it wrote."""
    with tempfile.NamedTemporaryFile("r+") as out, mock.patch.dict(os.environ, {"GITHUB_OUTPUT": out.name}):
        lifetime.main(list(argv))
        return dict(line.split("=", 1) for line in out.read().splitlines())


class ConfigTests(unittest.TestCase):
    def test_repo_config_is_valid(self):
        config = lifetime.load_config()
        self.assertEqual(lifetime.check_config(config), [])

    def test_detects_bad_config(self):
        bad = dict(APP, lifetimeAppId="nope", platforms=["android", "windows"], androidPackage="", name="")
        errors = " | ".join(lifetime.check_config(bad))
        for expected in ("lifetimeAppId", "platforms", "androidPackage", "name"):
            self.assertIn(expected, errors)

    def test_ios_bundle_id_may_contain_hyphens(self):
        self.assertEqual(lifetime.check_config(dict(APP, iosBundleId="com.example.my-app2")), [])
        # Android application IDs may not.
        self.assertIn("androidPackage", " ".join(lifetime.check_config(dict(APP, androidPackage="com.example.my-app"))))
        for bad in ("myapp", "com..app", "com.example.app ", "com.exa mple.app", "com.example.app!"):
            with self.subTest(bad):
                self.assertIn("iosBundleId", " ".join(lifetime.check_config(dict(APP, iosBundleId=bad))))

    def test_identifier_only_required_for_listed_platforms(self):
        ios_only = dict(APP, platforms=["ios"])
        del ios_only["androidPackage"]
        self.assertEqual(lifetime.check_config(ios_only), [])

    def test_wrong_types_are_reported_not_raised(self):
        self.assertEqual(len(lifetime.check_config(["not", "an", "object"])), 1)
        bad = dict(APP, id=7, name=None, lifetimeAppId=1, environment=[], platforms="android", iosBundleId={})
        errors = " | ".join(lifetime.check_config(bad))
        for expected in ("id", "name", "lifetimeAppId", "environment", "platforms"):
            self.assertIn(expected, errors)
        self.assertIn("platforms", " ".join(lifetime.check_config(dict(APP, platforms=[{"x": 1}]))))

    def test_unreadable_config_is_a_pipeline_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "app.json"
            with self.assertRaises(lifetime.PipelineError):
                lifetime.load_config(path)
            path.write_text("{not json")
            with self.assertRaises(lifetime.PipelineError) as ctx:
                lifetime.load_app(path)
            self.assertIn("not valid JSON", str(ctx.exception))

    def test_annotations_are_single_line(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            lifetime.error("HTTP 500 <html>\r\n::warning::injected 100%")
        self.assertEqual(err.getvalue(), "::error::HTTP 500 <html>%0D%0A::warning::injected 100%25\n")

    def test_plan_outputs(self):
        outputs = run_cli("plan", "--platform", "ios", "--environment", "default")
        self.assertEqual(outputs["id"], lifetime.load_config()["id"])
        self.assertEqual(outputs["environment"], lifetime.load_config()["environment"])
        self.assertEqual((outputs["android"], outputs["ios"]), ("false", "true"))
        self.assertEqual(outputs["ios_bundle_id"], lifetime.load_config()["iosBundleId"])

    def test_plan_environment_override(self):
        self.assertEqual(run_cli("plan", "--environment", "Testing")["environment"], "Testing")

    def test_app_is_not_an_argument(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            lifetime.main(["plan", "--app", "other-app"])

    def test_outputs_reject_newlines(self):
        with self.assertRaises(lifetime.PipelineError):
            lifetime.set_outputs(version="1.0\nfile=/etc/passwd")


class LifeTimeClientTests(unittest.TestCase):
    def test_host_normalisation_and_ci(self):
        for host in ("lt.example.com", "https://lt.example.com/", " lt.example.com/ "):
            self.assertEqual(lifetime.LifeTime(host, "t").base, "https://lt.example.com/lifetimeapi/rest/v2")
        # A host name that merely starts with "http" is still a bare host; the scheme is always https.
        self.assertEqual(lifetime.LifeTime("httpbin.example.com", "t").base, "https://httpbin.example.com/lifetimeapi/rest/v2")
        self.assertEqual(lifetime.LifeTime("http://LT.Example.com", "t").base, "https://lt.example.com/lifetimeapi/rest/v2")
        for bad in ("", "https://", "user@lt.example.com", "lt example.com"):
            with self.subTest(bad), self.assertRaises(lifetime.PipelineError):
                lifetime.LifeTime(bad, "t")
        self.assertEqual(lifetime.ci({"NativePlatform": "iOS"}, "nativePlatform"), "iOS")
        self.assertIsNone(lifetime.ci(None, "x"))

    def _download(self, url):
        lt = lifetime.LifeTime("lt.example.com", "secret")
        with mock.patch.object(lifetime._opener, "open", side_effect=RuntimeError("stop")) as opened:
            with self.assertRaises(RuntimeError):
                lt.download(url, os.devnull)
        return opened.call_args[0][0]

    def test_token_only_sent_to_lifetime_and_never_on_redirect(self):
        req = self._download("/files/app.aab")
        self.assertEqual(req.full_url, "https://lt.example.com/files/app.aab")
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), "Bearer secret")
        self.assertNotIn("Authorization", req.headers)  # headers would be copied to a redirect target
        cdn = self._download("https://cdn.example.net/app.aab")
        self.assertNotIn("Authorization", {**cdn.headers, **cdn.unredirected_hdrs})

    def test_token_sent_to_lifetime_host_in_any_case(self):
        req = self._download("https://LT.example.com/files/app.aab")
        self.assertEqual(req.unredirected_hdrs.get("Authorization"), "Bearer secret")

    def test_timeouts_and_dropped_connections_are_pipeline_errors(self):
        lt = lifetime.LifeTime("lt.example.com", "secret")
        for failure in (socket.timeout("timed out"), ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), urllib.error.URLError("dns")):
            with self.subTest(failure), mock.patch.object(lifetime._opener, "open", side_effect=failure):
                with self.assertRaises(lifetime.PipelineError):
                    lt.get("/environments/")
                with self.assertRaises(lifetime.PipelineError) as ctx:
                    lt.download("https://cdn.example.net/app.aab", os.devnull)
                self.assertNotIn("secret", str(ctx.exception))

    def test_refuses_non_https_download(self):
        lt = lifetime.LifeTime("lt.example.com", "secret")
        for url in ("http://lt.example.com/app.aab", "file:///etc/passwd"):
            with self.subTest(url), self.assertRaises(lifetime.PipelineError):
                lt.download(url, os.devnull)

    def _get_with_response(self, body, status=200):
        response = mock.MagicMock(status=status)
        response.read.return_value = body
        response.__enter__.return_value = response
        lt = lifetime.LifeTime("lt.example.com", "secret")
        with mock.patch.object(lifetime._opener, "open", return_value=response):
            return lt.get("/environments/")

    def test_get_parses_json_and_handles_no_content(self):
        self.assertEqual(self._get_with_response(b'[{"Key": "k"}]'), (200, [{"Key": "k"}]))
        self.assertEqual(self._get_with_response(b"", status=204), (204, None))

    def test_get_rejects_non_json_response(self):
        with self.assertRaises(lifetime.PipelineError) as ctx:
            self._get_with_response(b"<html>Sign in</html>")
        self.assertIn("LIFETIME_HOST", str(ctx.exception))

    def test_get_http_error_is_a_pipeline_error(self):
        lt = lifetime.LifeTime("lt.example.com", "secret")
        err = urllib.error.HTTPError("https://lt.example.com", 401, "Unauthorized", {}, io.BytesIO(b"bad token"))
        with mock.patch.object(lifetime._opener, "open", side_effect=err), self.assertRaises(lifetime.PipelineError) as ctx:
            lt.get("/environments/")
        self.assertIn("HTTP 401", str(ctx.exception))

    def test_mixed_case_lifetime_keys(self):
        lt = lifetime.LifeTime("lt.example.com", "secret")
        with mock.patch.object(lt, "get", return_value=(200, [{"key": "k1", "name": "Production"}])):
            self.assertEqual(lt.environment_key("Production"), ("k1", "Production"))
        app = {"kind": "Mobile", "name": "App", "appStatusInEnvs": [{"environmentKey": "k1", "mobileAppsStatus": [{"nativePlatform": "iOS"}]}]}
        with mock.patch.object(lt, "get", return_value=(200, app)):
            _, _, mobile = lt.mobile_status("k1", "app", "ios")
        self.assertEqual(mobile, {"nativePlatform": "iOS"})

    def test_refuses_redirect_to_http(self):
        handler = lifetime._HttpsOnlyRedirects()
        req = urllib.request.Request("https://lt.example.com/x")
        with self.assertRaises(lifetime.PipelineError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://evil.example/x")


class FakeLifeTime:
    """Stands in for LifeTime: serves one mobile build and 'downloads' a prepared file."""

    def __init__(self, binary, mobile=None, link="https://cdn.example.net/app"):
        self.binary, self.link = binary, link
        self.mobile = {"NativePlatform": None, "VersionNumber": "1.0", "IsConfigured": True, "HasBinaryAvailable": True}
        self.mobile.update(mobile or {})

    def environment_key(self, name):
        return "env-key", name

    def mobile_status(self, env_key, app_key, platform):
        mobile = dict(self.mobile, NativePlatform=lifetime.LIFETIME_PLATFORM[platform])
        return {"Name": "App"}, {"BaseApplicationVersionLabel": "1.0.0"}, mobile

    def download_link(self, env_key, app_key, binary_type):
        return self.link

    def download(self, url, dest):
        Path(dest).write_bytes(Path(self.binary).read_bytes())


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        patches = [
            mock.patch.object(lifetime, "load_app", return_value=dict(APP)),
            mock.patch("sys.stderr"),
            mock.patch("sys.stdout"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def aab(self, package="com.example.app"):
        path = self.dir / "src.aab"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("base/manifest/AndroidManifest.xml", proto_manifest(package, "30201", "3.2.1"))
        return path

    def ipa(self, profile):
        path = self.dir / "src.ipa"
        with zipfile.ZipFile(path, "w") as z:
            info = {"CFBundleIdentifier": "com.example.app", "CFBundleShortVersionString": "3.2.1", "CFBundleVersion": "321"}
            z.writestr("Payload/App.app/Info.plist", plistlib.dumps(info))
            z.writestr("Payload/App.app/embedded.mobileprovision", mobileprovision(**profile))
        return path

    def fetch(self, fake, platform="android", *extra):
        with mock.patch.object(lifetime, "lifetime_client", return_value=fake):
            return run_cli("fetch", "--platform", platform, "--out", str(self.dir / "out"), *extra)

    def assertFetchFails(self, fake, platform="android"):
        with self.assertRaises(SystemExit):
            self.fetch(fake, platform)

    def test_android_success(self):
        outputs = self.fetch(FakeLifeTime(self.aab()))
        self.assertEqual((outputs["identifier"], outputs["version"], outputs["build"]), ("com.example.app", "3.2.1", "30201"))
        self.assertTrue(outputs["file"].endswith("my-app-3.2.1-30201.aab"))

    def test_identifier_mismatch(self):
        self.assertFetchFails(FakeLifeTime(self.aab(package="com.other.app")))

    def test_unreadable_identifier_fails(self):
        apk = self.dir / "src.apk"
        with zipfile.ZipFile(apk, "w") as z:
            z.writestr("AndroidManifest.xml", b"binary-xml")
        with mock.patch.object(lifetime.shutil, "which", return_value=None), mock.patch.dict(os.environ, {"ANDROID_HOME": ""}):
            self.assertFetchFails(FakeLifeTime(apk))

    def test_no_binary_or_untagged(self):
        self.assertFetchFails(FakeLifeTime(self.aab(), mobile={"HasBinaryAvailable": False}))
        self.assertFetchFails(FakeLifeTime(self.aab(), link=None))

    def test_ios_requires_app_store_profile(self):
        self.assertFetchFails(FakeLifeTime(self.ipa({"ProvisionsAllDevices": True})), "ios")
        self.assertFetchFails(FakeLifeTime(self.ipa({"Entitlements": {"get-task-allow": True}})), "ios")
        outputs = self.fetch(FakeLifeTime(self.ipa({"Name": "Dist", "TeamIdentifier": ["T"]})), "ios")
        self.assertTrue(outputs["file"].endswith(".ipa"))

    def test_ios_rejects_expired_profile(self):
        self.assertFetchFails(FakeLifeTime(self.ipa({"Name": "Dist", "ExpirationDate": datetime.datetime(2020, 1, 1)})), "ios")
        outputs = self.fetch(FakeLifeTime(self.ipa({"Name": "Dist", "ExpirationDate": datetime.datetime(2999, 1, 1)})), "ios")
        self.assertTrue(outputs["file"].endswith("my-app-3.2.1-321.ipa"))

    def test_apk_served_instead_of_aab(self):
        apk = self.dir / "src.apk"
        with zipfile.ZipFile(apk, "w") as z:
            z.writestr("AndroidManifest.xml", b"binary-xml")
        meta = {"identifier": "com.example.app", "versionName": "3.2.1", "versionCode": "30201"}
        with mock.patch.object(lifetime, "inspect_apk", return_value=meta):
            outputs = self.fetch(FakeLifeTime(apk))
        self.assertTrue(outputs["file"].endswith("my-app-3.2.1-30201.apk"))

    def test_download_that_is_not_an_archive(self):
        junk = self.dir / "junk.bin"
        junk.write_bytes(b"<html>error</html>")
        self.assertFetchFails(FakeLifeTime(junk))

    def test_unsafe_version_rejected(self):
        self.assertFetchFails(FakeLifeTime(self.aab_with_version("3.2.1;rm -rf")))

    def aab_with_version(self, version_name):
        path = self.dir / "ver.aab"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("base/manifest/AndroidManifest.xml", proto_manifest("com.example.app", "30201", version_name))
        return path

    def test_template_placeholder_is_refused_before_calling_lifetime(self):
        placeholder = dict(APP, lifetimeAppId=lifetime.PLACEHOLDER_APP_ID)
        with mock.patch.object(lifetime, "load_app", return_value=placeholder), mock.patch.object(lifetime, "lifetime_client") as client:
            for argv in (["fetch", "--platform", "android"], ["status"]):
                with self.subTest(argv), self.assertRaises(SystemExit):
                    lifetime.main(argv)
            client.assert_not_called()

    def test_status_lists_every_environment(self):
        class Status:
            def environments(self):
                return [("k1", "Development"), ("k2", "Production"), ("k3", "Sandbox")]

            def app_status(self, env_key, app_key):
                if env_key == "k1":
                    return {}, None
                if env_key == "k3":
                    raise lifetime.PipelineError("LifeTime GET failed: HTTP 403")
                mobile = [{"nativePlatform": "Android", "VersionNumber": "3.2.1", "IsConfigured": True, "HasBinaryAvailable": True}]
                return {}, {"MobileAppsStatus": mobile}

        with tempfile.NamedTemporaryFile("r+") as out, mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": out.name}):
            with mock.patch.object(lifetime, "lifetime_client", return_value=Status()):
                lifetime.main(["status"])
            table = out.read()
        self.assertIn("| Development | not deployed | - | - |", table)
        self.assertIn("Android 3.2.1 (configured, binary)", table)
        self.assertIn("| Sandbox | - | - | LifeTime GET failed: HTTP 403 |", table)

    def test_dry_run_does_not_download(self):
        fake = FakeLifeTime(self.aab())
        fake.download = mock.Mock()
        outputs = self.fetch(fake, "android", "--dry-run")
        fake.download.assert_not_called()
        self.assertEqual(outputs["file"], "")


if __name__ == "__main__":
    unittest.main()
