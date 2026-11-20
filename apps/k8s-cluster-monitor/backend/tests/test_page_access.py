"""공통 계정/페이지 권한. 모든 변경은 새 인메모리 DB의 시험 계정만 사용한다."""
import sqlite3
import time
import pytest
from fastapi.testclient import TestClient
from app import auth,db,models
from app.main import app

@pytest.fixture
def isolated(monkeypatch):
    conn=models.get_connection(':memory:')
    monkeypatch.setattr(db,'_connection',conn)
    auth.ensure_seed(conn)
    client=TestClient(app)
    assert client.post('/api/login',json={'username':auth.DEFAULT_USERNAME,'password':auth.DEFAULT_PASSWORD}).status_code==200
    yield client,conn
    conn.close()

def add_reader(client,username='page-reader',pages=None):
    body={'username':username,'password':'test-reader-123','role':'viewer','pages':pages}
    result=client.post('/api/ui/users',json=body)
    assert result.status_code==201,result.text
    reader=TestClient(app)
    assert reader.post('/api/login',json={'username':username,'password':'test-reader-123'}).status_code==200
    return reader

def test_old_users_remain_admin_without_hash_changes(isolated):
    owner,conn=isolated
    before=conn.execute('SELECT password_hash,salt FROM users WHERE username=?',(auth.DEFAULT_USERNAME,)).fetchone()
    conn.execute('DELETE FROM user_access');conn.commit()
    auth.ensure_seed(conn)
    after=conn.execute('SELECT password_hash,salt FROM users WHERE username=?',(auth.DEFAULT_USERNAME,)).fetchone()
    assert before==after
    assert owner.get('/api/ui/session').json()['role']=='admin'

def test_new_viewer_cannot_create_users_or_modify_system_state(isolated):
    owner,_=isolated
    reader=add_reader(owner,pages=['overview','health'])
    assert reader.get('/api/ui/session').json()['canManageUsers'] is False
    assert reader.get('/api/users').status_code==403
    assert reader.post('/api/users',json={'username':'escalation','password':'new-test-123','role':'admin'}).status_code==403
    assert reader.get('/api/schedule').status_code==403
    assert reader.put('/api/schedule',json={'cron':None,'enabled_domains':[]}).status_code==403
    assert reader.post('/api/runs/health').status_code==403
    assert reader.post('/api/loadtest',json={}).status_code==403

def test_unauthorized_domain_and_unfiltered_queries_do_not_leak(isolated):
    owner,conn=isolated
    now=time.time()
    for domain in ['health','security']:
        conn.execute('INSERT INTO runs(domain,started_at,finished_at,status,critical_count,warning_count,info_count) VALUES(?,?,?,?,?,?,?)',(domain,now,now,'ok',0,0,1))
        conn.execute('INSERT INTO findings(fingerprint,domain,check_id,severity,title,detail,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?)',(domain+'-test',domain,'test','info','test','data',now,now))
    conn.commit()
    reader=add_reader(owner,pages=['overview','health'])
    assert reader.get('/api/findings?domain=security').status_code==403
    assert reader.get('/api/runs?domain=security').status_code==403
    assert {r['domain'] for r in reader.get('/api/findings').json()}=={'health'}
    assert {r['domain'] for r in reader.get('/api/runs').json()}=={'health'}
    assert set(reader.get('/api/runs/latest').json())=={'health'}
    assert {r['domain'] for r in reader.get('/api/findings/trend').json()}=={'health'}

def test_grants_and_roles_change_existing_sessions_immediately(isolated):
    owner,_=isolated
    reader=add_reader(owner,pages=['health'])
    assert reader.get('/api/findings?domain=health').status_code==200
    assert owner.patch('/api/ui/users/page-reader',json={'role':'viewer','pages':[]}).status_code==200
    assert reader.get('/api/findings?domain=health').status_code==403
    assert reader.get('/api/ui/session').status_code==200
    assert owner.patch('/api/ui/users/page-reader',json={'role':'admin','pages':['users']}).status_code==200
    assert reader.get('/api/ui/users').status_code==200

def test_last_administrator_protected(isolated):
    owner,_=isolated
    result=owner.patch('/api/ui/users/'+auth.DEFAULT_USERNAME,json={'role':'viewer','pages':[]})
    assert result.status_code==409

def test_password_requires_confirmation_and_valid_current_password(isolated):
    owner,_=isolated
    reader=add_reader(owner,pages=[])
    body={'current_password':'test-reader-123','new_password':'changed-test-123','confirm_password':'different-test'}
    assert reader.post('/api/ui/password',json=body).status_code==400
    body['confirm_password']=body['new_password'];body['current_password']='wrong'
    assert reader.post('/api/ui/password',json=body).status_code==401
    body['current_password']='test-reader-123'
    assert reader.post('/api/ui/password',json=body).status_code==200
    assert reader.get('/api/ui/session').status_code==200
    assert TestClient(app).post('/api/login',json={'username':'page-reader','password':'changed-test-123'}).status_code==200

def test_unknown_pages_do_not_create_account(isolated):
    owner,conn=isolated
    result=owner.post('/api/ui/users',json={'username':'bad-grant','password':'new-test-123','role':'viewer','pages':['unknown']})
    assert result.status_code==400
    assert conn.execute('SELECT COUNT(*) FROM users WHERE username=?',('bad-grant',)).fetchone()[0]==0


def test_deleted_user_cannot_keep_a_live_session(isolated):
    owner,conn=isolated
    reader=add_reader(owner,pages=['overview'])
    conn.execute('DELETE FROM users WHERE username=?',('page-reader',));conn.commit()
    assert reader.get('/api/ui/session').status_code==401
