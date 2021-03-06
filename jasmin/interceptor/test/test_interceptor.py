from datetime import datetime
from hashlib import md5
from twisted.internet import reactor, defer
from twisted.trial import unittest
from twisted.spread import pb
from jasmin.interceptor.interceptor import InterceptorPB
from jasmin.interceptor.proxies import InterceptorPBProxy, InvalidRoutableObject, InvalidScriptObject
from jasmin.interceptor.configs import InterceptorPBClientConfig, InterceptorPBConfig
from twisted.cred import portal
from jasmin.tools.cred.portal import JasminPBRealm
from jasmin.tools.spread.pb import JasminPBPortalRoot
from twisted.cred.checkers import AllowAnonymousAccess, InMemoryUsernamePasswordDatabaseDontUse
from jasmin.tools.proxies import ConnectError
from jasmin.routing.Routables import SimpleRoutablePDU, RoutableSubmitSm, RoutableDeliverSm, Routable
from jasmin.vendor.smpp.pdu.operations import SubmitSM, DeliverSM
from jasmin.routing.jasminApi import *
from testfixtures import LogCapture

class InterceptorPBTestCase(unittest.TestCase):
    def setUp(self, authentication = False):
        # Initiating config objects without any filename
        # will lead to setting defaults and that's what we
        # need to run the tests
        self.InterceptorPBConfigInstance = InterceptorPBConfig()
        self.InterceptorPBConfigInstance.authentication = authentication

        # Launch the interceptor pb server
        pbRoot = InterceptorPB()
        pbRoot.setConfig(self.InterceptorPBConfigInstance)

        p = portal.Portal(JasminPBRealm(pbRoot))
        if not authentication:
            p.registerChecker(AllowAnonymousAccess())
        else:
            c = InMemoryUsernamePasswordDatabaseDontUse()
            c.addUser('test_user', md5('test_password').digest())
            p.registerChecker(c)
        jPBPortalRoot = JasminPBPortalRoot(p)
        self.IPBServer = reactor.listenTCP(0, pb.PBServerFactory(jPBPortalRoot))
        self.ipbPort = self.IPBServer.getHost().port

        # Test fixtures
        self.SubmitSMPDU = SubmitSM(
            source_addr='20203060',
            destination_addr='20203060',
            short_message='MT hello world',
        )
        self.DeliverSMPDU = DeliverSM(
            source_addr='20203060',
            destination_addr='20203060',
            short_message='MO hello world',
        )
        self.connector = Connector('abc')
        self.user = User(1, Group(100), 'username', 'password')

        # Routables fixtures
        self.routable_simple = SimpleRoutablePDU(self.connector, self.SubmitSMPDU, self.user, datetime.now())

        # Scripts fixtures
        self.script_generic = InterceptorScript('somevar = "something in MOIS"')
        self.script_3_second = InterceptorScript('import time;time.sleep(3)')
        self.script_syntax_error = InterceptorScript('somevar = sssss')
        self.script_http_status = InterceptorScript('http_status = 404')
        self.script_smpp_status = InterceptorScript('smpp_status = 64')

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.IPBServer.stopListening()

class IntercentorPBProxyTestCase(InterceptorPBProxy, InterceptorPBTestCase):
    @defer.inlineCallbacks
    def tearDown(self):
        yield InterceptorPBTestCase.tearDown(self)
        yield self.disconnect()

class AuthenticatedTestCases(IntercentorPBProxyTestCase):
    @defer.inlineCallbacks
    def setUp(self, authentication=False):
        yield IntercentorPBProxyTestCase.setUp(self, authentication=True)

    @defer.inlineCallbacks
    def test_connect_success(self):
        yield self.connect('127.0.0.1', self.ipbPort, 'test_user', 'test_password')

    @defer.inlineCallbacks
    def test_connect_failure(self):
        try:
            yield self.connect('127.0.0.1', self.ipbPort, 'test_anyuser', 'test_wrongpassword')
        except ConnectError, e:
            self.assertEqual(str(e), 'Authentication error test_anyuser')
        except Exception, e:
            self.assertTrue(False, "ConnectError not raised, got instead a %s" % type(e))
        else:
            self.assertTrue(False, "ConnectError not raised")

        self.assertFalse(self.isConnected)

    @defer.inlineCallbacks
    def test_connect_non_anonymous(self):
        try:
            yield self.connect('127.0.0.1', self.ipbPort)
        except ConnectError, e:
            self.assertEqual(str(e), 'Anonymous connection is not authorized !')
        except Exception, e:
            self.assertTrue(False, "ConnectError not raised, got instead a %s" % type(e))
        else:
            self.assertTrue(False, "ConnectError not raised")

        self.assertFalse(self.isConnected)

class RunScriptTestCases(IntercentorPBProxyTestCase):
    @defer.inlineCallbacks
    def test_standard(self):
        yield self.connect('127.0.0.1', self.ipbPort)

        yield self.run_script(self.script_generic, self.routable_simple)

    @defer.inlineCallbacks
    def test_routable_type(self):
        yield self.connect('127.0.0.1', self.ipbPort)

        try:
            yield self.run_script(self.script_generic, 'anything')
        except InvalidRoutableObject, e:
            self.assertEqual(str(e), 'anything')
        except Exception, e:
            self.assertTrue(False, "InvalidRoutableObject not raised, got instead a %s" % type(e))
        else:
            self.assertTrue(False, "InvalidRoutableObject not raised")

    @defer.inlineCallbacks
    def test_script_type(self):
        yield self.connect('127.0.0.1', self.ipbPort)

        try:
            yield self.run_script('anything', self.routable_simple)
        except InvalidScriptObject, e:
            self.assertEqual(str(e), 'anything')
        except Exception, e:
            self.assertTrue(False, "InvalidScriptObject not raised, got instead a %s" % type(e))
        else:
            self.assertTrue(False, "InvalidScriptObject not raised")

    @defer.inlineCallbacks
    def test_return_value(self):
        yield self.connect('127.0.0.1', self.ipbPort)

        # Return pickled routable on success
        r = yield self.run_script(self.script_generic, self.routable_simple)
        r = self.unpickle(r)
        self.assertTrue(isinstance(r, Routable))

        # Return false on syntax error
        r = yield self.run_script(self.script_syntax_error, self.routable_simple)
        self.assertFalse(r)

        # Return http and smpp status if defined (!= 0) in the script
        # Changing http or smpp status would imply getting both defined
        r = yield self.run_script(self.script_http_status, self.routable_simple)
        self.assertEqual(404, r['http_status'])
        self.assertEqual(255, r['smpp_status'])
        r = yield self.run_script(self.script_smpp_status, self.routable_simple)
        self.assertEqual(520, r['http_status'])
        self.assertEqual(64, r['smpp_status'])

    @defer.inlineCallbacks
    def test_slow_script_logging(self):
        lc = LogCapture("jasmin-interceptor")

        yield self.connect('127.0.0.1', self.ipbPort)

        # Log script with ~3s execution time
        yield self.run_script(self.script_3_second, self.routable_simple)
        # Assert last logged line:
        lc.records[len(lc.records) - 1].getMessage().index('Execution delay [3s] for script [import time;time.sleep(3)].')

        # Set threshold to 5s
        self.InterceptorPBConfigInstance.log_slow_script = 5
        # Dont log script with ~3s execution time
        yield self.run_script(self.script_3_second, self.routable_simple)
        # Assert last logged line:
        lc.records[len(lc.records) - 1].getMessage().index('with routable with pdu: PDU')
