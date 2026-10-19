from http.client import HTTPConnection
import json
import tempfile
import threading
import unittest

from street_skeleton.server import Server


class ServerTests(unittest.TestCase):
    def test_local_session_and_rejected_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            server=Server(0,directory)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                connection=HTTPConnection("127.0.0.1",server.server_port,timeout=5)
                connection.request("GET","/")
                response=connection.getresponse()
                html=response.read().decode()
                self.assertEqual(response.status,200)
                self.assertIn(server.token,html)
                connection.request("POST","/api/run",body=b"bad")
                response=connection.getresponse();response.read()
                self.assertEqual(response.status,403)
                connection.close()
                connection=HTTPConnection("127.0.0.1",server.server_port,timeout=5)
                headers={"X-Street-Token":server.token}
                connection.request("GET","/api/state",headers=headers)
                response=connection.getresponse()
                self.assertFalse(json.loads(response.read())["busy"])
                connection.request("POST","/api/run?tolerance=99",body=b"bad",headers=headers)
                response=connection.getresponse();response.read()
                self.assertEqual(response.status,400)
                connection.request("GET","/api/download?file=../../secret",headers=headers)
                response=connection.getresponse();response.read()
                self.assertEqual(response.status,404)
                connection.request("GET","/",headers={"Host":"other.example"})
                response=connection.getresponse();response.read()
                self.assertEqual(response.status,403)
                connection.close()
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
