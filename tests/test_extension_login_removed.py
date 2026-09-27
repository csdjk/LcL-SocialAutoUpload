import pytest
from tests.test_douyin_credential_import import local_app, STATE


@pytest.mark.parametrize('method,path', [('get','/edge-connect'), ('get','/edge-connect.js'),
    ('post','/accounts/edge-link'), ('get','/accounts/edge-link'), ('delete','/accounts/edge-link'),
    ('post','/accounts/import-edge')])
def test_extension_entrypoints_are_removed(local_app, method, path):
    client = local_app[0].test_client()
    assert getattr(client, method)(path).status_code == 404


def test_old_extension_token_cannot_authorize_manual_import(local_app):
    response = local_app[0].test_client().post('/accounts/import-douyin',
        headers={'X-SAU-Edge-Link':'old-extension-token'}, json={'credentials':STATE})
    assert response.status_code == 403
