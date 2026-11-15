"""The authority and a server certificate made in a temporary data folder are
read back by openssl where it is installed — the chain verifies, the server
certificate names the addresses asked for, the authority is a CA limited to
private addresses and `.local` names, and a certificate for a public name
issued by it is rejected; the authority is made once and kept, its key file
readable by its owner alone, and the server certificate is made again for new
addresses and kept for the same ones (behaviour#secure-at-home)."""

import os
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import unittest

from mirutube import certificates


def openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True, text=True)


class Certificates(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mirutube-ca-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.ca = os.path.join(self.folder, "mirutube-ca.crt")

    def make(self, names):
        return certificates.server_certificate(self.folder, names)

    def test_python_loads_and_trusts_them(self):
        cert, key = self.make(["192.168.1.20", "localhost", "box.local"])
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(cert, key)
        client = ssl.create_default_context(cafile=self.ca)
        self.assertEqual(len(client.get_ca_certs()), 1)

    def test_authority_is_made_once_and_kept(self):
        first = certificates.authority(self.folder)
        second = certificates.authority(self.folder)
        self.assertEqual(first, second)
        if sys.platform != "win32":
            mode = stat.S_IMODE(os.stat(os.path.join(self.folder, "mirutube-ca.key")).st_mode)
            self.assertEqual(mode, 0o600)

    def test_server_certificate_kept_for_the_same_names_and_made_again_for_new(self):
        cert, _ = self.make(["192.168.1.20", "localhost"])
        with open(cert) as f:
            before = f.read()
        self.make(["localhost", "192.168.1.20"])
        with open(cert) as f:
            self.assertEqual(f.read(), before)
        self.make(["192.168.1.21", "localhost"])
        with open(cert) as f:
            self.assertNotEqual(f.read(), before)
        if sys.platform != "win32":
            mode = stat.S_IMODE(os.stat(os.path.join(self.folder, "server.key")).st_mode)
            self.assertEqual(mode, 0o600)

    def test_only_permitted_names_are_put_in(self):
        names = certificates.default_names(["192.168.1.20", "8.8.8.8", "example.com"])
        self.assertIn("192.168.1.20", names)
        self.assertIn("localhost", names)
        self.assertNotIn("8.8.8.8", names)
        self.assertNotIn("example.com", names)
        self.assertTrue(all(certificates._permitted(n) for n in names))


@unittest.skipIf(shutil.which("openssl") is None, "no openssl to read the certificates back")
class ReadByOpenssl(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="mirutube-ca-")
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.ca = os.path.join(self.folder, "mirutube-ca.crt")
        self.cert, _ = certificates.server_certificate(self.folder, ["192.168.1.20", "localhost", "box.local"])

    def test_chain_verifies(self):
        r = openssl("verify", "-CAfile", self.ca, "-purpose", "sslserver", self.cert)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for host in ("box.local", "localhost"):
            r = openssl("verify", "-CAfile", self.ca, "-verify_hostname", host, self.cert)
            self.assertEqual(r.returncode, 0, host + r.stdout + r.stderr)
        r = openssl("verify", "-CAfile", self.ca, "-verify_ip", "192.168.1.20", self.cert)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = openssl("verify", "-CAfile", self.ca, "-verify_ip", "192.168.1.21", self.cert)
        self.assertNotEqual(r.returncode, 0)

    def test_authority_is_a_constrained_ca(self):
        text = openssl("x509", "-in", self.ca, "-noout", "-text").stdout
        self.assertIn("CA:TRUE", text)
        self.assertIn("Certificate Sign", text)
        self.assertIn("Name Constraints: critical", text)
        for permitted in ("DNS:local", "DNS:localhost", "IP:192.168.0.0/255.255.0.0", "IP:10.0.0.0/255.0.0.0"):
            self.assertIn(permitted, text)
        server = openssl("x509", "-in", self.cert, "-noout", "-text").stdout
        self.assertIn("TLS Web Server Authentication", server)
        self.assertIn("CA:FALSE", server)

    def test_a_public_name_is_rejected(self):
        # what the name constraints are for: even signed by the authority, a
        # certificate for a site on the internet does not verify
        d, public, ca_cert = certificates.authority(self.folder)
        sd, spublic = certificates._new_key()
        names = ["example.com", "8.8.8.8"]
        der, _ = certificates._certificate(
            certificates._name("example.com"), certificates._subject_of(ca_cert), spublic, d, public, 30,
            [certificates._extension(certificates.OID_SUBJECT_ALT_NAME,
                                     certificates._seq(*[certificates._general_name(n) for n in names]))])
        rogue = os.path.join(self.folder, "rogue.crt")
        with open(rogue, "w") as f:
            f.write(certificates._pem("CERTIFICATE", der))
        r = openssl("verify", "-CAfile", self.ca, rogue)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("subtree", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
