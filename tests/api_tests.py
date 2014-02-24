#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import os
import sys
import contextlib
import unittest
import httpretty
import difflib
import subprocess
import tempfile
# mock is part of python3.3
try:
    import unittest.mock
except ImportError:
    import mock

from string import Template
import oscs
import osc
import operator
import re
import pprint

PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
else:
    string_types = basestring,

class OBS:
    """
    Class trying to simulate a simple OBS
    """
    responses = { }

    def __init__(self):
        """
        Initialize the configuration and create basic OBS instance
        """

        self.reset_config()

    def reset_config(self):
        self._clear_responses()

    def _clear_responses(self):
        """
        Reset predefined responses
        """
        self.responses = { 'GET': {}, 'PUT': {}, 'POST': {}, 'ALL': {} }
        # Add methods to manipulate reviews
        self._request_review()
        # Add methods to search requests
        self._request_search()

    def _pretty_callback(self, request, uri, headers):
        """
        Custom callback for HTTPretty.

        It mocks requests and replaces calls with either xml, content of file,
        function call or first item in array of those.

        :param request: request as provided to callback function by HTTPretty
        :param uri: uri as provided to callback function by HTTPretty
        :param headers: headers as provided to callback function by HTTPretty
        """

        # Get path
        path = re.match( r'.*localhost([^?]*)(\?.*)?',uri).group(1)
        reply = None
        # Try to find a fallback
        if self.responses['ALL'].has_key(path):
            reply = self.responses['ALL'][path]
        # Try to find a specific method
        if self.responses[request.method].has_key(path):
            reply = self.responses[request.method][path]
        # We have something to reply with
        if reply:
            # It's a list, so take the first
            if isinstance(reply, list):
                reply = reply.pop(0)
            # It's string
            if isinstance(reply, string_types):
                # It's XML
                if reply.startswith('<'):
                    return (200, headers, reply)
                # It's fixture
                else:
                    return (200, headers, _get_fixture_content(reply))
            # All is left is callback function
            else:
                return (200, headers, reply(self.responses, request, uri))
        # No possible response found
        else:
            if len(path) == 0:
                path = uri
            raise BaseException("No response for {0} on {1} provided".format(request.method,path))

    # Initial request data
    requests_data = { '123': { 'request': 'new', 'review': 'accepted',
                               'who': 'Admin', 'by': 'group', 'id': '123',
                               'by_who': 'opensuse-review-team',
                               'package': 'gcc' },
                      '321': { 'request': 'review', 'review': 'new',
                               'who': 'Admin', 'by': 'group', 'id': '321',
                               'by_who': 'factory-staging',
                               'package': 'puppet' }
                    }

    def _request_review(self):
        """
        Register requests methods
        """

        # Load template
        tmpl = Template(self._get_fixture_content('request_review.xml'))

        # What happens when we try to change the review
        def review_change(responses, request, uri):
            rq_id = re.match( r'.*/([0-9]+)',uri).group(1)
            args = self.requests_data[rq_id]
            # Adding review
            if request.querystring.has_key(u'cmd') and request.querystring[u'cmd'] == [u'addreview']:
                self.requests_data[rq_id]['request'] = 'review'
                self.requests_data[rq_id]['review']  = 'new'
            # Changing review
            if request.querystring.has_key(u'cmd') and request.querystring[u'cmd'] == [u'changereviewstate']:
                self.requests_data[rq_id]['request'] = 'new'
                self.requests_data[rq_id]['review']  = request.querystring[u'newstate'][0]
            # Project review
            if request.querystring.has_key(u'by_project'):
                self.requests_data[rq_id]['by']      = 'project'
                self.requests_data[rq_id]['by_who']  = request.querystring[u'by_project'][0]
            # Group review
            if request.querystring.has_key(u'by_group'):
                self.requests_data[rq_id]['by']      = 'group'
                self.requests_data[rq_id]['by_who']  = request.querystring[u'by_group'][0]
            responses['GET']['/request/' + rq_id]  = tmpl.substitute(self.requests_data[rq_id])
            return responses['GET']['/request/' + rq_id]

        # Register methods for all requests
        for rq in self.requests_data:
            # Static response for gets (just filling template from local data)
            self.responses['GET']['/request/' + rq] = tmpl.substitute(self.requests_data[rq])
            # Interpret other requests
            self.responses['ALL']['/request/' + rq] = review_change

    def _request_search(self):
        """
        Allows searching for requests
        """
        def request_search(responses, request, uri):
            # Searching for requests that has open review for staging group
            if request.querystring.has_key(u'match') and request.querystring[u'match'][0] == u"state/@name='review' and review[@by_group='factory-staging' and @state='new']":
                rqs = []
                # Itereate through all requests
                for rq in self.requests_data:
                    # Find the ones matching the condition
                    if self.requests_data[rq]['request'] == 'review' and self.requests_data[rq]['review'] == 'new' and self.requests_data[rq]['by'] == 'group' and self.requests_data[rq]['by_who'] == 'factory-staging':
                        rqs.append(rq)
                # Create response
                ret_str  = '<collection matches="' + str(len(rqs)) + '">'
                for rq in rqs:
                    ret_str += responses['GET']['/request/' + rq]
                ret_str += '</collection>'
                return ret_str
            # We are searching for something else, we don't know the answer
            raise BaseException("No search results defined for " + pprint.pformat(request.querystring))
        self.responses['GET']['/search/request'] = request_search

    def register_obs(self):
        """
        Register custom callback for HTTPretty
        """
        httpretty.register_uri(httpretty.GET,re.compile(r'/.*localhost.*/'),body=self._pretty_callback)
        httpretty.register_uri(httpretty.PUT,re.compile(r'/.*localhost.*/'),body=self._pretty_callback)
        httpretty.register_uri(httpretty.POST,re.compile(r'/.*localhost.*/'),body=self._pretty_callback)
        self.reset_config()
        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            self.api = oscs.StagingAPI('http://localhost')

    def _get_fixtures_dir(self):
        """
        Return path for fixtures
        """
        return os.path.join(os.getcwd(), 'tests/fixtures')

    def _get_fixture_path(self, filename):
        """
        Return path for fixture
        """
        return os.path.join(self._get_fixtures_dir(), filename)

    def _get_fixture_content(self, filename):
        """
        Return content of fixture
        """
        response = open(self._get_fixture_path(filename), 'r')
        content = response.read()
        response.close()
        return content

class TestApiCalls(unittest.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    obs = OBS()

    def _get_fixtures_dir(self):
        """
        Return path for fixtures
        """
        return os.path.join(os.getcwd(), 'tests/fixtures')

    def _get_fixture_path(self, filename):
        return os.path.join(self._get_fixtures_dir(), filename)

    def _get_fixture_content(self, filename):
        response = open(self._get_fixture_path(filename), 'r')
        content = response.read()
        response.close()
        return content

    def _register_pretty_url_get(self, url, filename):
        """
        Register specified get url with specific filename in fixtures
        :param url: url address to "open"
        :param filename: name of the fixtures file
        """

        content = self._get_fixture_content(filename)

        httpretty.register_uri(httpretty.GET,
                               url,
                               body=content)


    def _register_pretty_url_post(self, url, filename):
        """
        Register specified post url with specific filename in fixtures
        :param url: url address to "open"
        :param filename: name of the fixtures file
        """

        response = open(os.path.join(self._get_fixtures_dir(), filename), 'r')
        content = response.read()
        response.close()

        httpretty.register_uri(httpretty.POST,
                               url,
                               body=content)

    def setUp(self):
        """
        Initialize the configuration so the osc is happy
        """

        oscrc = os.path.join(self._get_fixtures_dir(), 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        os.environ['OSC_CONFIG'] = oscrc

    @httpretty.activate
    def test_ring_packages(self):
        """
        Validate the creation of the rings.
        """

        # our content in the XML files
        ring_packages = {
            'elem-ring-0': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-1': 'openSUSE:Factory:Rings:1-MinimalX',
        }

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Rings:0-Bootstrap',
                                      'ring-0-project.xml')
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Core',
                                      'ring-1-project.xml')

        # Create the api object
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')
        self.assertEqual(ring_packages, api.ring_packages)

    @httpretty.activate
    def test_dispatch_open_requests(self):
        """
        Test dispatching and closure of non-ring packages
        """

        # Register OBS
        self.obs.register_obs()
        # Get rid of open requests
        self.obs.api.dispatch_open_requests()
        # Check that we tried to close it
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'changereviewstate'])
        # Try it again
        self.obs.api.dispatch_open_requests()
        # This time there should be nothing to close
        self.assertEqual(httpretty.last_request().method, 'GET')

    @httpretty.activate
    def test_pseudometa_get_prj(self):
        """
        Test getting project metadata from YAML in project description
        """
        rq = { 'id': '123', 'package': 'test-package' }

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Staging:test1/_meta',
                                      'staging-project-meta.xml')
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Staging:test2/_meta',
                                      'staging-project-broken-meta.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Ensure the output is equal to what we expect
        data = api.get_prj_pseudometa('openSUSE:Factory:Staging:test1')
        for i in rq.keys():
            self.assertEqual(rq[i],data['requests'][0][i])

        data = api.get_prj_pseudometa('openSUSE:Factory:Staging:test2')
        self.assertEqual(len(data['requests']),0)

    @httpretty.activate
    def test_list_projects(self):
        """
        List projects and their content
        """

        prjlist = [
            'openSUSE:Factory:Staging:A',
            'openSUSE:Factory:Staging:B',
            'openSUSE:Factory:Staging:C',
            'openSUSE:Factory:Staging:D'
        ]

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/search/project/id?match=starts-with(@name,\'openSUSE:Factory:Staging:\')',
                                      'staging-project-list.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Compare the results
        self.assertEqual(prjlist,
                        api.get_staging_projects())

    @httpretty.activate
    def test_open_requests(self):
        """
        Test searching for open requests
        """

        requests = []

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/search/request?match=state/@name=\'review\'+and+review[@by_group=\'factory-staging\'+and+@state=\'new\']',
                                      'open-requests.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # get the open requests
        requests = api.get_open_requests()
        count = len(requests)

        # Compare the results, we only care now that we got 2 of them not the content
        self.assertEqual(2, count)

    @httpretty.activate
    def test_get_package_information(self):
        """
        Test if we get proper project, name and revision from the staging informations
        """

        package_info = {'project': 'devel:wine',
                        'rev': '7b98ac01b8071d63a402fa99dc79331c',
                        'srcmd5': '7b98ac01b8071d63a402fa99dc79331c',
                        'package': 'wine'}

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Staging:B/wine',
                                      'linksource.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Compare the results, we only care now that we got 2 of them not the content
        self.assertEqual(package_info,
                         api.get_package_information('openSUSE:Factory:Staging:B', 'wine'))

    @httpretty.activate
    def test_create_package_container(self):
        """
        Test if the uploaded _meta is correct
        """

        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        httpretty.register_uri(
            httpretty.PUT, "http://localhost/source/openSUSE:Factory:Staging:B/wine/_meta")

        api.create_package_container('openSUSE:Factory:Staging:B', 'wine')
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title/><description/></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

        api.create_package_container('openSUSE:Factory:Staging:B', 'wine', disable_build=True)
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title /><description /><build><disable /></build></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

    @httpretty.activate
    def test_review_handling(self):
        """
        Test whether accepting/creating reviews behaves correctly
        """

        # Register OBS
        self.obs.register_obs()

        # Add review
        self.obs.api.add_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'addreview'])
        # Try to readd, should do anything
        self.obs.api.add_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'GET')
        # Accept review
        self.obs.api.set_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'changereviewstate'])
        # Try to accept it again should do anything
        self.obs.api.set_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'GET')
        # But we should be able to reopen it
        self.obs.api.add_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'addreview'])


    @httpretty.activate
    def test_check_project_status_green(self):
        """
        Test checking project status
        """

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/build/green/_result',
                                      'build-results-green.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Check print output
        self.assertEqual(api.gather_build_status("green"), None)

    @httpretty.activate
    def test_check_project_status_red(self):
        """
        Test checking project status
        """

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/build/red/_result',
                                      'build-results-red.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Check print output
        self.assertEqual(api.gather_build_status('red'), ['red', [{'path': 'standard/x86_64', 'state': 'building'}],
                                                          [{'path': 'standard/i586', 'pkg': 'glibc', 'state': 'broken'},
                                                           {'path': 'standard/i586', 'pkg': 'openSUSE-images', 'state': 'failed'}]])

    def test_bootstrap_copy(self):
        import osclib.freeze_command
        fc = osclib.freeze_command.FreezeCommand('http://localhost')

        fp = self._get_fixture_path('staging-meta-for-bootstrap-copy.xml')
        fixture = subprocess.check_output('/usr/bin/xmllint --format %s' % fp, shell=True)

        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(fc.prj_meta_for_bootstrap_copy('openSUSE:Factory:Staging:A'))
        f.close()

        output = subprocess.check_output('/usr/bin/xmllint --format %s' % f.name, shell=True)

        for line in difflib.unified_diff(fixture.split("\n"), output.split("\n")):
            print(line)
        self.assertEqual(output, fixture)

# Here place all mockable functions
@contextlib.contextmanager
def mock_generate_ring_packages():
    with  mock.patch('oscs.StagingAPI._generate_ring_packages', return_value={
        'elem-ring-0': 'openSUSE:Factory:Rings:0-Bootstrap',
        'elem-ring-1': 'openSUSE:Factory:Rings:1-MinimalX'}):
        yield
