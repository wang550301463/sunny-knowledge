import unittest

import httpx

from test_identity import login_module


class TransportRegression(unittest.TestCase):
    def test_only_configured_public_origin_routes_to_docker_gateway(self):
        observed=[]
        def receive(request):
            observed.append((str(request.url),request.headers['host'],request.content))
            return httpx.Response(200,json={'ok':True})
        transport=login_module.GatewayTransport('http://localhost:18180','http://gateway:8080',httpx.MockTransport(receive))
        with httpx.Client(transport=transport) as client:
            response=client.post('http://localhost:18180/idp/path?q=1',content=b'body')
            self.assertEqual(response.status_code,200)
            self.assertEqual(observed,[('http://gateway:8080/idp/path?q=1','localhost:18180',b'body')])
            with self.assertRaises(ValueError):client.get('http://outside.example/path')

if __name__=='__main__':unittest.main()