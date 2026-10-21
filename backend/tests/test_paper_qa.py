"""Synthetic HTTP/RAG regressions; isolated child DB, no real channel or material."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest


class PaperQaIsolation(unittest.TestCase):
    def test_isolated_paper_qa(self):
        with tempfile.TemporaryDirectory(prefix='paper-qa-contract-') as folder:
            root = Path(folder).resolve()
            env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC') if key in os.environ}
            env.update(TEMP=str(root), TMP=str(root), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-B', str(Path(__file__).resolve()), '--fixture', str(root)],
                                    cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())


def fixture(root):
    assert root.resolve() == Path.cwd().resolve() and root.name.startswith('paper-qa-contract-')
    config = root / 'fixture.yaml'
    config.write_text(json.dumps({'storage': {'data_dir': str(root/'data'), 'upload_dir': str(root/'uploads'), 'vector_dir': str(root/'vectors')},
                                  'security': {'secret_key': 'synthetic-only', 'pbkdf2_iterations': 1000}}), encoding='utf-8')
    os.environ['APP_CONFIG'] = str(config)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from contextlib import ExitStack
    from unittest.mock import patch
    import httpx
    from fastapi import Header
    from fastapi.testclient import TestClient
    from sqlalchemy import select, func
    from app.main import app
    from app.api.deps import CurrentUser, get_current_user
    from app.core.config import settings
    from app.db.session import Base, get_engine, get_session_factory
    from app.db.models import (Role, User, KnowledgeBase, UserKbAccess, Document, Chunk, ChatSession,
                               Message, Citation, LlmChannel, VERSIONING_TABLE_NAMES)
    from app.services import qa_service as qa
    from app.engines import llm_gateway as gateway
    from app.engines.keyword_index import KeywordIndex
    from app.engines.vector_store import vector_store
    settings.ensure_dirs()
    assert settings.db_path.resolve().is_relative_to(root)
    engine, factory = get_engine(), get_session_factory()
    tables = [table for table in Base.metadata.sorted_tables if table.name not in VERSIONING_TABLE_NAMES]

    def actor(x_test_user: str = Header(default='alice')):
        with factory() as db:
            user = db.scalar(select(User).where(User.username == x_test_user))
            return CurrentUser(id=user.id, username=user.username, display_name=user.display_name, role_code=user.role_code)

    app.dependency_overrides[get_current_user] = actor
    client = TestClient(app)

    class Contract(unittest.TestCase):
        def setUp(self):
            Base.metadata.drop_all(engine, tables=tables)
            Base.metadata.create_all(engine, tables=tables)
            KeywordIndex.invalidate(); vector_store.invalidate()
            self.stack = ExitStack(); self.addCleanup(self.stack.close)
            self.stack.enter_context(patch.object(qa, 'embed_query', return_value=[1., 0.]))
            self.stack.enter_context(patch.object(qa, 'get_embedding_identity', return_value=None))
            self.stack.enter_context(patch('httpx.post', side_effect=AssertionError('Unmocked network forbidden')))
            with factory() as db:
                db.add_all([Role(code=r, name=r) for r in ('user', 'operator', 'admin')])
                for name, role in [('alice','user'),('bob','user'),('admin','admin')]:
                    db.add(User(username=name, display_name=name, role_code=role, password_salt='', password_digest='', is_active=True))
                db.commit(); users = {u.username:u for u in db.scalars(select(User))}; self.users={k:v.id for k,v in users.items()}
                kb = KnowledgeBase(name='A论文库',owner_id=users['alice'].id,status='enabled')
                own = KnowledgeBase(name='A独立库',owner_id=users['alice'].id,status='enabled')
                foreign = KnowledgeBase(name='B私库',owner_id=users['bob'].id,status='enabled')
                db.add_all([kb,own,foreign]);db.flush(); self.kb,self.other,self.foreign=kb.id,own.id,foreign.id
                docs=[]; chunks=[]
                for scope, owner, text in [(kb,users['alice'], '多尺度变化检测研究方法。'+ '包含完整实验设计、区域划分和边界评价。'*60 + 'TAIL-EVIDENCE-9876'),
                                            (own,users['alice'],'OWN-OTHER-LIBRARY 多尺度变化检测其他独立库'),
                                            (foreign,users['bob'],'BOB-PRIVATE-ONLY 多尺度变化检测保密内容')]:
                    doc = Document(kb_id=scope.id,filename=f'论文{scope.id}.md',file_type='md',status='success',uploaded_by=owner.id)
                    db.add(doc);db.flush(); docs.append(doc.id)
                    chunk = Chunk(kb_id=scope.id,document_id=doc.id,seq_no=1,content=text,vector_json='[1.0,0.0]',meta_json='{}')
                    db.add(chunk);db.flush();chunks.append(chunk.id)
                self.docs,self.chunks=docs,chunks
                db.commit()

        def request(self, method, url, who='alice', **kwargs):
            return client.request(method, '/api'+url, headers={'x-test-user':who}, **kwargs)

        def ask(self, **extra):
            return self.request('POST','/qa/ask',json={'kb_id':self.kb,'question':'解释多尺度变化检测研究方法和实验设计',**extra})

        def channel(self):
            with factory() as db:
                db.add(LlmChannel(name='fixture',channel_type='online',base_url='http://127.0.0.1:9/v1',model_name='fixture',enabled=True,health_status='unknown'))
                db.commit()

        def test_private_libraries_reject_other_user_and_admin_before_model(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe') as call:
                for who in ('bob','admin'):
                    self.assertEqual(self.request('POST','/qa/ask',who=who,json={'kb_id':self.kb,'question':'变化检测'}).status_code,403)
                call.assert_not_called()

        def test_real_vector_store_and_keyword_hit_only_requested_library(self):
            response=self.ask();self.assertEqual(response.status_code,200,response.text); data=response.json()
            self.assertEqual(data['retrieval']['method'],'hybrid');self.assertGreater(data['retrieval']['vector_hits'],0)
            self.assertEqual({c['doc_id'] for c in data['citations']},{self.docs[0]})
            self.assertNotIn('BOB-PRIVATE-ONLY',response.text);self.assertNotIn('OWN-OTHER-LIBRARY',response.text)
            self.assertEqual(data['generation_status'],'unconfigured');self.assertIn('不是模型回答',data['answer'])

        def test_full_evidence_tail_is_sent_and_industry_prompt_is_not(self):
            self.channel(); captured=[]
            def answer(channel,messages,timeout_s=None):
                captured.append(messages);return '研究方法使用多尺度特征融合。[来源1]'
            with patch.object(qa,'_call_llm_safe',side_effect=answer): response=self.ask()
            self.assertEqual(response.status_code,200,response.text)
            prompt=captured[0][-1]['content']; self.assertIn('TAIL-EVIDENCE-9876',prompt)
            self.assertIn('实验依据与局限',prompt);self.assertNotIn('BOB-PRIVATE-ONLY',prompt);self.assertNotIn('OWN-OTHER-LIBRARY',prompt)
            self.assertEqual(response.json()['generation_status'],'generated');self.assertFalse(response.json()['degraded'])
            self.assertGreater(len(response.json()['citations'][0]['content_excerpt']),400)

        def test_cached_foreign_or_forged_hit_is_rebound_before_egress(self):
            self.channel(); foreign={'chunk_id':self.chunks[2],'doc_id':self.docs[2],'doc_name':'fake','content':'BOB-PRIVATE-ONLY','score':.99}
            forged={'chunk_id':self.chunks[0],'doc_id':self.docs[2],'doc_name':'fake','content':'LEAK','score':.9}
            with patch.object(qa.KeywordIndex,'search',return_value=[foreign,forged]), patch.object(qa.vector_store,'search',return_value=[]), patch.object(qa,'_call_llm_safe') as call:
                response=self.ask()
            self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['citations'],[]);call.assert_not_called()
            self.assertNotIn('BOB-PRIVATE-ONLY',response.text)

        def test_generation_errors_are_explicit_and_history_keeps_status(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe',return_value=qa.GenerationFailure('timeout')): response=self.ask()
            data=response.json();self.assertEqual(data['generation_status'],'unavailable');self.assertEqual(data['mode'],'degraded');self.assertIn('未能完成回答',data['answer'])
            history=self.request('GET',f'/qa/sessions/{data["session_id"]}/messages').json()
            self.assertEqual(history[-1]['generation_status'],'unavailable');self.assertIn('调用失败',history[-1]['notice'])

        def test_invalid_or_missing_citations_are_not_presented_as_model_answers(self):
            self.channel()
            for raw in ('未附来源的断言', '错误来源[来源99]', '不支持的来源[来源0]'):
                with self.subTest(raw=raw),patch.object(qa,'_call_llm_safe',return_value=raw):
                    data=self.ask().json();self.assertEqual(data['generation_status'],'invalid_citations');self.assertEqual(data['mode'],'degraded')
                    self.assertNotEqual(data['answer'],raw)

        def test_generation_failure_notice_is_actionable_and_never_echoes_unknown_errors(self):
            self.channel()
            for code, expected in [('timeout','响应超时'),('authentication','访问权限'),('rate_limited','限流'),('SECRET_RAW_UPSTREAM','调用失败')]:
                with self.subTest(code=code),patch.object(qa,'_call_llm_safe',return_value=qa.GenerationFailure(code)):
                    data=self.ask().json()
                    self.assertEqual(data['generation_status'],'unavailable')
                    self.assertIn(expected,data['notice']);self.assertIn(expected,data['answer'])
                    self.assertNotIn('SECRET_RAW_UPSTREAM',data['answer']);self.assertNotIn('SECRET_RAW_UPSTREAM',data['notice'])
                    self.assertTrue(data['citations'])

        def test_vector_failure_is_reported_as_keyword_not_vector_success(self):
            with patch.object(qa,'embed_query',side_effect=OSError('synthetic unavailable')): data=self.ask().json()
            self.assertEqual(data['retrieval']['method'],'keyword');self.assertEqual(data['retrieval']['vector_status'],'unavailable')
            self.assertEqual(data['retrieval']['vector_hits'],0);self.assertTrue(data['citations'])

        def test_session_cannot_be_reused_with_another_owned_library(self):
            data=self.ask().json()
            mismatch=self.ask(session_id=data['session_id'],kb_id=self.other)
            self.assertEqual(mismatch.status_code,409)
            history=self.request('GET',f'/qa/sessions/{data["session_id"]}/messages').json();self.assertEqual(len(history),2)

        def test_permissions_revoked_during_generation_abort_persistence(self):
            with factory() as db: db.add(UserKbAccess(kb_id=self.kb,role_code='user'));db.commit()
            self.channel()
            def revoke(*args,**kwargs):
                with factory() as db:
                    db.query(UserKbAccess).filter(UserKbAccess.kb_id==self.kb).delete();db.commit()
                return '只有原始证据。[来源1]'
            with patch.object(qa,'_call_llm_safe',side_effect=revoke):
                result=self.request('POST','/qa/ask',who='bob',json={'kb_id':self.kb,'question':'多尺度变化检测'})
            self.assertEqual(result.status_code,403)
            with factory() as db:self.assertEqual(db.scalar(select(func.count(Message.id))),0)

        def test_failover_checks_grant_again_before_egress(self):
            with factory() as db: db.add(UserKbAccess(kb_id=self.kb,role_code='user'));db.commit()
            self.channel();self.channel()
            def revoke(*args,**kwargs):
                with factory() as db: db.query(UserKbAccess).filter(UserKbAccess.kb_id==self.kb).delete();db.commit()
                return qa.GenerationFailure('timeout')
            with patch.object(qa,'_call_llm_safe',side_effect=revoke) as calls:
                response=self.request('POST','/qa/ask',who='bob',json={'kb_id':self.kb,'question':'多尺度变化检测'})
            self.assertEqual(calls.call_count,1,'Revoked evidence must not be sent to a fallback channel')
            self.assertEqual(response.status_code,403)
            with factory() as db:self.assertEqual(db.scalar(select(func.count(Message.id))),0)

        def test_failover_checks_expanded_neighbor_before_egress(self):
            with factory() as db:
                neighbor=Chunk(kb_id=self.kb,document_id=self.docs[0],seq_no=2,page_no=0,content='Neighbor evidence to revalidate.',vector_json='[0,1]')
                db.add(neighbor);db.commit();neighbor_id=neighbor.id
            self.channel();self.channel()
            def mutate(*args,**kwargs):
                with factory() as db: db.get(Chunk,neighbor_id).content='changed after first attempt';db.commit()
                return qa.GenerationFailure('timeout')
            with patch.object(qa,'_call_llm_safe',side_effect=mutate) as calls: response=self.ask()
            self.assertEqual(calls.call_count,1,'Expanded evidence must be checked, not just its citation anchor')
            self.assertEqual(response.status_code,409)
            with factory() as db:self.assertEqual(db.scalar(select(func.count(Message.id))),0)

        def test_failover_checks_document_readability_and_retrieval_exclusion(self):
            self.channel();self.channel()
            for change in ('status','excluded'):
                with self.subTest(change=change):
                    with factory() as db:
                        db.get(Document,self.docs[0]).status='success';db.get(Chunk,self.chunks[0]).meta_json='{}';db.commit()
                    KeywordIndex.invalidate();vector_store.invalidate()
                    def restrict(*args,**kwargs):
                        with factory() as db:
                            if change=='status':db.get(Document,self.docs[0]).status='failed'
                            else:db.get(Chunk,self.chunks[0]).meta_json='{"retrieval_excluded":true}'
                            db.commit()
                        return qa.GenerationFailure('timeout')
                    with patch.object(qa,'_call_llm_safe',side_effect=restrict) as calls: response=self.ask()
                    self.assertEqual(calls.call_count,1,'An unreadable source must not reach a fallback channel')
                    self.assertEqual(response.status_code,409)
            with factory() as db:self.assertEqual(db.scalar(select(func.count(Message.id))),0)

        def test_chunk_mutation_during_generation_aborts_persistence(self):
            self.channel()
            def mutate(*args,**kwargs):
                with factory() as db: db.get(Chunk,self.chunks[0]).content='new revision';db.commit()
                return '原始回答[来源1]'
            with patch.object(qa,'_call_llm_safe',side_effect=mutate): result=self.ask()
            self.assertEqual(result.status_code,409,result.text)
            with factory() as db:self.assertEqual(db.scalar(select(func.count(Message.id))),0)

        def test_neighbor_page_change_after_generation_blocks_saved_citation(self):
            with factory() as db:
                neighbor=Chunk(kb_id=self.kb,document_id=self.docs[0],seq_no=2,page_no=0,content='Stable text on changed page.',vector_json='[0,1]')
                db.add(neighbor);db.commit();neighbor_id=neighbor.id
            self.channel()
            def move_locator(*args,**kwargs):
                with factory() as db: db.get(Chunk,neighbor_id).page_no=99;db.commit()
                return '回答包含正确编号但来源位置已经过时。[来源1]'
            with patch.object(qa,'_call_llm_safe',side_effect=move_locator):response=self.ask()
            self.assertEqual(response.status_code,409)
            with factory() as db:self.assertEqual(db.scalar(select(func.count(Message.id))),0)

        def test_history_rejects_forged_cross_library_citation(self):
            data=self.ask().json()
            with factory() as db:
                cite=db.scalar(select(Citation).where(Citation.message_id==data['message_id']));cite.document_id=self.docs[2];db.commit()
            result=self.request('GET',f'/qa/sessions/{data["session_id"]}/messages')
            self.assertEqual(result.status_code,409);self.assertNotIn('BOB-PRIVATE-ONLY',result.text)

        def test_sessions_endpoint_filters_by_library_and_denies_other_scope(self):
            self.ask();self.ask(kb_id=self.other)
            rows=self.request('GET',f'/qa/sessions?kb_id={self.kb}').json();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kb_id'],self.kb)
            self.assertEqual(self.request('GET',f'/qa/sessions?kb_id={self.foreign}').status_code,403)

        def test_followup_query_uses_same_session_question(self):
            data=self.ask().json()
            with patch.object(qa,'embed_query',return_value=[1.,0.]) as encode:
                result=self.ask(session_id=data['session_id'],question='那它有什么局限？')
            self.assertEqual(result.status_code,200);self.assertIn('多尺度变化检测',encode.call_args.args[0]);self.assertIn('局限',encode.call_args.args[0])

        def test_evidence_windows_bind_neighbor_hashes_and_keep_page_scope(self):
            with factory() as db:
                anchor=db.get(Chunk,self.chunks[0]);anchor.page_no=3;anchor.seq_no=4
                neighbor=Chunk(kb_id=self.kb,document_id=self.docs[0],page_no=3,seq_no=5,content='NEIGHBOR-METHOD-SUPPORT',vector_json='[0,1]')
                different=Chunk(kb_id=self.kb,document_id=self.docs[0],page_no=4,seq_no=6,content='OTHER-PAGE-SENTINEL',vector_json='[0,1]')
                db.add_all([neighbor,different]);db.commit()
                source={'chunk_id':anchor.id,'doc_id':anchor.document_id,'seq_no':4,'page_no':3,'content':anchor.content,'doc_name':'paper.md'}
                result=qa._expand_evidence(db,self.kb,[source],None)
                self.assertIn('NEIGHBOR-METHOD-SUPPORT',result[0]['content']);self.assertNotIn('OTHER-PAGE-SENTINEL',result[0]['content'])
                self.assertEqual(set(result[0]['evidence_bindings']),{anchor.id,neighbor.id})
                self.assertEqual(result[0]['page_no'],3)

        def test_funding_only_is_not_method_evidence_but_funding_queries_can_read_it(self):
            hit={'content':'Acknowledgements: supported by foundation grant IDs. '*8}
            self.assertFalse(qa._substantive_evidence(hit,'研究方法是什么'))
            self.assertTrue(qa._substantive_evidence(hit,'论文受哪些基金资助'))

        def test_keyword_index_rejects_corrupt_cross_library_chunk_owner(self):
            with factory() as db:
                db.add(Chunk(kb_id=self.kb,document_id=self.docs[2],seq_no=2,content='CORRUPT-FOREIGN-MARKER',vector_json='[1,0]'))
                db.commit()
            hits=KeywordIndex().search('CORRUPT-FOREIGN-MARKER',self.kb,top_k=20)
            self.assertFalse(any(hit['doc_id']==self.docs[2] for hit in hits))

        def test_context_budget_and_source_diversity(self):
            hits=[{'chunk_id':i,'doc_id':1 if i<6 else i,'doc_name':'synthetic','content':'x'*1000} for i in range(1,9)]
            chosen=qa._select_evidence(hits,5,8000)
            self.assertEqual(len(chosen),5);self.assertGreater(len({c['doc_id'] for c in chosen}),1)
            self.assertLessEqual(len(qa._source_block(chosen,8000)),8000)

        def test_gateway_endpoint_does_not_duplicate_existing_completions(self):
            self.assertEqual(gateway._endpoint('https://example.invalid/v1/chat/completions'),'https://example.invalid/v1/chat/completions')
            self.assertEqual(gateway._endpoint('https://example.invalid/v1/'),'https://example.invalid/v1/chat/completions')
            self.assertEqual(gateway._endpoint('http://127.0.0.1:11434/api/chat',ollama=True),'http://127.0.0.1:11434/api/chat')
            for url in ('file:///tmp/key','https://user:secret@example.invalid/v1','https://example.invalid/v1?token=secret'):
                with self.subTest(url=url),self.assertRaises(gateway.ChannelUnavailable):gateway._endpoint(url)

        def test_gateway_error_never_contains_upstream_secret_body(self):
            request=httpx.Request('POST','https://example.invalid/v1/chat/completions')
            response=httpx.Response(401,request=request,json={'error':'SECRET-UPSTREAM-BODY'})
            with patch('httpx.post',return_value=response),self.assertRaises(gateway.ChannelUnavailable) as error:
                gateway._openai_chat('https://example.invalid/v1','synthetic-token','fixture',[],.3,3)
            self.assertEqual(error.exception.code,'authentication');self.assertNotIn('SECRET',str(error.exception))

        def add_overview_documents(self, count):
            ids=[]
            with factory() as db:
                for number in range(count):
                    doc=Document(kb_id=self.kb,filename=f'synthetic-paper-{number}.pdf',file_type='pdf',
                                 status='success',uploaded_by=self.users['alice'])
                    db.add(doc);db.flush();ids.append(doc.id)
                    db.add_all([
                        Chunk(kb_id=self.kb,document_id=doc.id,seq_no=1,page_no=1,
                              content='SYNTHETIC JOURNAL HEADER AND AUTHORS. '*5,meta_json='{}'),
                        Chunk(kb_id=self.kb,document_id=doc.id,seq_no=2,page_no=1,
                              content=f'Abstract—We propose SYNTHETIC-METHOD-{number} for multi-temporal image alignment. '+
                                      'Controlled experiments use held-out regions to measure boundary quality and temporal consistency. '*10,meta_json='{}'),
                        Chunk(kb_id=self.kb,document_id=doc.id,seq_no=3,page_no=1,
                              content='Our contributions include a shared representation and reproducible evaluation protocol. '*5,meta_json='{}'),
                    ])
                db.commit()
            return ids

        def test_builtin_overview_questions_do_not_depend_on_topic_similarity_gate(self):
            docs=self.add_overview_documents(3);self.channel()
            questions=['概括这些论文的核心研究问题与贡献','比较不同论文的方法、优势和局限',
                       '解释论文中的实验设计与评价指标','有哪些值得进一步研究的问题？']
            for question in questions:
                with self.subTest(question=question), patch.object(qa.KeywordIndex,'search',return_value=[]) as kw, \
                     patch.object(qa.vector_store,'search',return_value=[]) as vec, \
                     patch.object(qa,'_call_llm_safe',return_value='基于论文证据解释研究目标与方法。[来源1]') as call:
                    response=self.ask(question=question)
                    self.assertEqual(response.status_code,200,response.text);data=response.json()
                    self.assertEqual(data['generation_status'],'generated')
                    self.assertEqual(data['retrieval']['method'],'overview')
                    self.assertEqual(data['retrieval']['total_documents'],4)
                    self.assertEqual(data['retrieval']['covered_documents'],4)
                    self.assertEqual(data['retrieval']['vector_status'],'not_used')
                    self.assertEqual({c['doc_id'] for c in data['citations']},set([self.docs[0]]+docs))
                    self.assertNotIn('BOB-PRIVATE-ONLY',response.text);self.assertNotIn('OWN-OTHER-LIBRARY',response.text)
                    self.assertIn('并非全文通读',data['answer'])
                    self.assertIn('覆盖 4/4',call.call_args.args[1][-1]['content'])
                    kw.assert_not_called();vec.assert_not_called()

        def test_overview_distributes_budget_across_papers_instead_of_first_six_chunks(self):
            from app.db.models import SysParam
            docs=self.add_overview_documents(8);self.channel()
            with factory() as db:
                db.add_all([SysParam(key='qa.max_context_chunks',value='6'),SysParam(key='qa.max_context_chars',value='4000')]);db.commit()
            with patch.object(qa,'_call_llm_safe',return_value='不同论文提供不同的证据。[来源1]') as call:
                response=self.ask(question='概括这些论文的核心研究问题与贡献')
            self.assertEqual(response.status_code,200,response.text);data=response.json()
            self.assertEqual(len(data['citations']),9)
            self.assertEqual({c['doc_id'] for c in data['citations']},set([self.docs[0]]+docs))
            self.assertTrue(all(len(c['content_excerpt'])>=160 for c in data['citations']))
            self.assertLessEqual(sum(len(c['content_excerpt'])+len(c['doc_name'])+60 for c in data['citations']),4000)
            self.assertFalse(data['retrieval']['coverage_limited'])
            self.assertTrue(all(c['chunk_id'] for c in data['citations']))

        def test_unrelated_specific_single_paper_and_negated_questions_never_fall_back_to_overview(self):
            self.channel()
            questions=['怎样烘焙巧克力蛋糕？','概括这些论文中的巧克力蛋糕做法',
                       '总结这篇论文的贡献','不要概括这些论文的核心研究问题与贡献',
                       'Compare these papers on quantum battery design', '2024年的论文采用哪些方法？']
            for question in questions:
                with self.subTest(question=question), patch.object(qa.KeywordIndex,'search',return_value=[]) as kw, \
                     patch.object(qa.vector_store,'search',return_value=[]) as vec, patch.object(qa,'_call_llm_safe') as call:
                    response=self.ask(question=question)
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(response.json()['generation_status'],'no_evidence')
                    self.assertNotEqual(response.json()['retrieval']['method'],'overview')
                    self.assertEqual(response.json()['citations'],[]);call.assert_not_called()
                    kw.assert_called_once();vec.assert_called_once()

        def test_overview_excludes_unready_excluded_and_cross_library_documents(self):
            docs=self.add_overview_documents(3);self.channel()
            with factory() as db:
                db.get(Document,docs[0]).status='parsing'
                for chunk in db.scalars(select(Chunk).where(Chunk.document_id==docs[1])):
                    chunk.meta_json='{"retrieval_excluded":true}'
                db.add(Chunk(kb_id=self.kb,document_id=self.docs[2],seq_no=2,page_no=1,
                             content='Abstract—CORRUPT-FOREIGN-OVERVIEW '*20,meta_json='{}'))
                db.commit()
            with patch.object(qa,'_call_llm_safe',return_value='说明可读取的论文。[来源1]'):
                response=self.ask(question='概括这些论文的核心研究问题与贡献')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual({c['doc_id'] for c in response.json()['citations']},{self.docs[0],docs[2]})
            self.assertNotIn('CORRUPT-FOREIGN-OVERVIEW',response.text)
            self.assertEqual(response.json()['retrieval']['total_documents'],2)

        def test_overview_empty_library_does_not_call_a_model(self):
            with factory() as db:
                kb=KnowledgeBase(name='EMPTY-OVERVIEW',owner_id=self.users['alice'],status='enabled')
                db.add(kb);db.commit();kid=kb.id
            self.channel()
            with patch.object(qa,'_call_llm_safe') as call:
                response=self.ask(kb_id=kid,question='概括这些论文的核心研究问题与贡献')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['retrieval']['method'],'overview')
            self.assertEqual(response.json()['retrieval']['total_documents'],0)
            self.assertEqual(response.json()['generation_status'],'no_evidence');call.assert_not_called()

        def test_overview_unavailable_model_keeps_all_sources_outside_the_failure_answer(self):
            docs=self.add_overview_documents(8);self.channel()
            with patch.object(qa,'_call_llm_safe',return_value=qa.GenerationFailure('timeout')):
                response=self.ask(question='概括这些论文的核心研究问题与贡献')
            self.assertEqual(response.status_code,200,response.text);data=response.json()
            self.assertEqual(data['generation_status'],'unavailable')
            self.assertEqual(data['mode'],'degraded');self.assertIn('响应超时',data['answer'])
            self.assertEqual(data['retrieval']['total_documents'],9)
            self.assertEqual(data['retrieval']['covered_documents'],len({c['doc_id'] for c in data['citations']}))
            self.assertFalse(data['retrieval']['coverage_limited'])
            self.assertEqual(data['retrieval']['covered_documents'],9)
            self.assertNotIn('Abstract—',data['answer'])
            self.assertNotIn('SYNTHETIC-METHOD',data['answer'])
            history=self.request('GET',f"/qa/sessions/{data['session_id']}/messages").json()
            self.assertIn('并非全文通读',history[-1]['content'])
            self.assertEqual(history[-1]['generation_status'],'unavailable')

        def test_overview_source_changes_during_generation_still_reject_persistence(self):
            self.add_overview_documents(1);self.channel()
            def answer(*args,**kwargs):
                with factory() as db:
                    db.get(Chunk,self.chunks[0]).content='CHANGED AFTER GENERATION START';db.commit()
                return '不能保存过时证据。[来源1]'
            with patch.object(qa,'_call_llm_safe',side_effect=answer):
                response=self.ask(question='概括这些论文的核心研究问题与贡献')
            self.assertEqual(response.status_code,409,response.text)
            with factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(Message)),0)

        def test_overview_document_cap_reports_partial_instead_of_claiming_full_library(self):
            self.add_overview_documents(66);self.channel()
            with patch.object(qa,'_call_llm_safe',return_value='只归纳本次覆盖的资料。[来源1]'):
                response=self.ask(question='概括这些论文的核心研究问题与贡献')
            self.assertEqual(response.status_code,200,response.text);data=response.json()
            self.assertEqual(data['retrieval']['total_documents'],67)
            self.assertEqual(data['retrieval']['covered_documents'],64)
            self.assertEqual(len(data['citations']),64)
            self.assertTrue(data['retrieval']['coverage_limited'])
            self.assertIn('部分覆盖',data['answer'])
            self.assertTrue(all(c['similarity']==0 for c in data['citations']), 'Overview evidence has no measured query similarity')

        def test_overview_uses_the_exact_current_version_scope_in_every_source_query(self):
            self.add_overview_documents(2)
            scope={'test_scope':'only-current-chunk'}
            with factory() as db, patch.object(qa.versions,'retrieval_scope',return_value=scope), \
                 patch.object(qa.versions,'current_chunk_filter',return_value=Chunk.id.in_([self.chunks[0]])) as filtered:
                sources,returned,options,trace=qa.retrieve_evidence(db,self.kb,'概括这些论文的核心研究问题与贡献')
                self.assertIs(returned,scope)
                self.assertEqual({source['chunk_id'] for source in sources},{self.chunks[0]})
                self.assertEqual(trace['total_documents'],1)
                self.assertGreaterEqual(filtered.call_count,3)
                self.assertTrue(all(call.kwargs['scope'] is scope for call in filtered.call_args_list))

        def test_overview_section_selection_avoids_title_metadata_and_keeps_bound_page(self):
            docs=self.add_overview_documents(1)
            with factory() as db:
                sources,scope,options,trace=qa.retrieve_evidence(db,self.kb,'概括这些论文的核心研究问题与贡献')
                selected=next(source for source in sources if source['doc_id']==docs[0])
                self.assertIn('SYNTHETIC-METHOD-0',selected['content'])
                self.assertNotIn('SYNTHETIC JOURNAL HEADER',selected['content'])
                self.assertEqual(selected['page_no'],1)
                self.assertGreaterEqual(len(selected['evidence_bindings']),2)
                qa._assert_evidence_fresh(db,self.kb,sources,
                    {cid:sha for source in sources for cid,sha in source['evidence_bindings'].items()},scope)

        def test_failed_generation_keeps_evidence_out_of_answer_body(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe',return_value=qa.GenerationFailure('timeout')):
                response=self.ask()
            self.assertEqual(response.status_code,200,response.text);data=response.json()
            self.assertEqual(data['generation_status'],'unavailable')
            self.assertIn('响应超时',data['answer'])
            self.assertNotIn('TAIL-EVIDENCE-9876',data['answer'])
            self.assertNotIn('### [来源',data['answer'])
            self.assertIn('TAIL-EVIDENCE-9876',data['citations'][0]['content_excerpt'])

        def test_large_question_and_generated_answer_remain_complete(self):
            self.channel()
            question='解释变化检测。'+'详细研究说明。'*6000+'QUESTION-TAIL-NOT-TRUNCATED'
            answer='综合结论与实验比较。'*5000+'OUTPUT-TAIL-NOT-TRUNCATED[来源1]'
            with patch.object(qa,'_call_llm_safe',return_value=answer) as call:
                response=self.ask(question=question)
            self.assertEqual(response.status_code,200,response.text[:400]);data=response.json()
            self.assertEqual(data['generation_status'],'generated')
            self.assertEqual(data['answer'],answer)
            self.assertIn(question,call.call_args.args[1][-1]['content'])
            history=self.request('GET',f"/qa/sessions/{data['session_id']}/messages").json()
            self.assertEqual(history[0]['content'],question)
            self.assertEqual(history[1]['content'],answer)

        def stream_events(self, response):
            self.assertEqual(response.status_code,200,response.text[:500])
            self.assertIn('application/x-ndjson',response.headers['content-type'])
            events=[json.loads(line) for line in response.text.splitlines() if line.strip()]
            self.assertTrue(events)
            terminal=[event for event in events if event['event'] in ('result','error')]
            self.assertEqual(len(terminal),1)
            self.assertIs(terminal[0],events[-1])
            return events

        def test_qa_progress_stream_waits_for_complete_validated_answer(self):
            import time
            self.channel()
            answer='FINAL-SUMMARY '*6000+'[来源1]'
            question='详细分析变化检测。'*5000+'INPUT-TAIL'
            def generate(channel,messages,timeout_s=None,control=None):
                self.assertIsNotNone(control);self.assertGreaterEqual(timeout_s,180)
                self.assertIn(question,messages[-1]['content'])
                control.progress('generating',received_chars=70000,ignored_raw_reasoning='PRIVATE-REASONING')
                time.sleep(.15)
                return answer
            with patch.object(qa,'_call_llm_safe',side_effect=generate):
                response=self.request('POST','/qa/ask/stream',json={'kb_id':self.kb,'question':question})
            events=self.stream_events(response);self.assertEqual(events[-1]['event'],'result')
            self.assertEqual(events[-1]['data']['answer'],answer)
            self.assertEqual(events[-1]['data']['generation_status'],'generated')
            self.assertNotIn('PRIVATE-REASONING',response.text)
            self.assertTrue(any(event.get('received_chars')==70000 for event in events[:-1]))
            for event in events[:-1]:self.assertNotIn('answer',event);self.assertNotIn('data',event)
            with factory() as db:
                self.assertEqual(db.scalar(select(Message).where(Message.role=='user')).content,question)
                self.assertEqual(db.scalar(select(Message).where(Message.role=='assistant')).content,answer)

        def test_stream_rejects_foreign_scope_before_invoking_model(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe') as model:
                response=self.request('POST','/qa/ask/stream',who='bob',json={'kb_id':self.kb,'question':'概括这些论文的核心研究问题与贡献'})
            self.assertEqual(response.status_code,403);model.assert_not_called()

        def test_stream_cancellation_before_completion_cannot_save_partial_answer(self):
            from app.services.generation_runtime import GenerationCancelled
            self.channel()
            def generate(channel,messages,timeout_s=None,control=None):
                control.progress('generating',received_chars=42)
                control.cancel();control.checkpoint()
                raise AssertionError('Cancelled control did not stop')
            with patch.object(qa,'_call_llm_safe',side_effect=generate):
                events=self.stream_events(self.request('POST','/qa/ask/stream',json={'kb_id':self.kb,'question':'解释变化检测'}))
            self.assertEqual(events[-1]['event'],'error');self.assertEqual(events[-1]['status'],499)
            with factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(Message)),0)

        def test_stream_unknown_exception_never_returns_exception_text(self):
            self.channel()
            with patch('app.api.qa.run_qa',side_effect=RuntimeError('PRIVATE-UPSTREAM-SECRET')):
                response=self.request('POST','/qa/ask/stream',json={'kb_id':self.kb,'question':'解释变化检测'})
            events=self.stream_events(response)
            self.assertEqual(events[-1]['status'],500)
            self.assertNotIn('PRIVATE-UPSTREAM-SECRET',response.text)

        def test_both_translation_directions_preserve_unlimited_input_and_complete_output(self):
            self.channel()
            for target,source,translated in [('en','第一段中文。\n\n第二段保留公式 E=mc² 与数值3.14。','First paragraph.\n\nSecond paragraph preserves E=mc² and 3.14.'),
                                              ('zh','First paragraph.\n\nPreserve E=mc² and 3.14.','第一段。\n\n保留 E=mc² 与3.14。')]:
                text=source*2500+'[来源1]';expected=translated*2500+'[来源1]'
                with self.subTest(target=target):
                    captured=[]
                    def generate(channel,messages,timeout_s=None,control=None):
                        captured.append(messages);self.assertEqual(messages[-1]['content'],text)
                        return expected
                    with patch.object(qa,'_call_llm_safe',side_effect=generate):
                        response=self.request('POST','/qa/translate/stream',json={'text':text,'target_language':target})
                    data=self.stream_events(response)[-1]['data']
                    self.assertEqual(data['translation'],expected);self.assertEqual(data['target_language'],target)
                    self.assertIsNone(data['source_message_id'])
                    self.assertIn('只输出完整译文',captured[0][0]['content'])
            with factory() as db:self.assertEqual(db.scalar(select(func.count()).select_from(Message)),0)

        def test_translate_existing_answer_checks_ownership_and_never_overwrites_it(self):
            self.channel();original='Original controlled experiment. [来源1]'
            with patch.object(qa,'_call_llm_safe',return_value=original):answer=self.ask().json()
            mid=answer['message_id']
            for who in ('bob','admin'):
                with patch.object(qa,'_call_llm_safe') as model:
                    rejected=self.request('POST','/qa/translate/stream',who=who,json={'message_id':mid,'target_language':'zh'})
                    self.assertEqual(rejected.status_code,404);model.assert_not_called()
            with patch.object(qa,'_call_llm_safe',return_value='原始对照实验。[来源1]'):
                events=self.stream_events(self.request('POST','/qa/translate/stream',json={'message_id':mid,'target_language':'zh'}))
            self.assertEqual(events[-1]['data']['source_message_id'],mid)
            with factory() as db:
                self.assertEqual(db.get(Message,mid).content,original)
                self.assertEqual(db.scalar(select(func.count()).select_from(Message)),2)

        def test_translation_revocation_or_source_edit_discards_model_output(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe',return_value='Original body [来源1]'):answer=self.ask().json()
            mid=answer['message_id']
            def edited(channel,messages,timeout_s=None,control=None):
                with factory() as db:db.get(Message,mid).content='Changed source [来源1]';db.commit()
                return '不应显示的旧译文。[来源1]'
            with patch.object(qa,'_call_llm_safe',side_effect=edited):
                response=self.request('POST','/qa/translate/stream',json={'message_id':mid,'target_language':'zh'})
            events=self.stream_events(response);self.assertEqual(events[-1]['status'],409)
            self.assertNotIn('不应显示的旧译文',response.text)
            def disabled(channel,messages,timeout_s=None,control=None):
                with factory() as db:db.get(User,self.users['alice']).is_active=False;db.commit()
                return '不应交付的译文'
            with patch.object(qa,'_call_llm_safe',side_effect=disabled):
                response=self.request('POST','/qa/translate/stream',json={'text':'source','target_language':'zh'})
            events=self.stream_events(response);self.assertEqual(events[-1]['status'],403)
            self.assertNotIn('不应交付的译文',response.text)

        def test_translation_invalid_requests_and_missing_markers_are_not_accepted(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe') as model:
                for request in ({'text':'x','message_id':1,'target_language':'en'}, {'target_language':'zh'},
                                {'text':'   ','target_language':'zh'}, {'text':'x','target_language':'fr'}):
                    self.assertEqual(self.request('POST','/qa/translate/stream',json=request).status_code,422)
                model.assert_not_called()
            with patch.object(qa,'_call_llm_safe',return_value='Missing source markers'):
                response=self.request('POST','/qa/translate/stream',json={'text':'原文[来源1]','target_language':'en'})
            events=self.stream_events(response);self.assertEqual(events[-1]['status'],502)
            self.assertNotIn('Missing source markers',response.text)

        def test_translation_unconfigured_and_timeout_errors_have_no_fake_translation(self):
            events=self.stream_events(self.request('POST','/qa/translate/stream',json={'text':'Original text','target_language':'zh'}))
            self.assertEqual(events[-1]['status'],503)
            self.channel()
            with patch.object(qa,'_call_llm_safe',return_value=qa.GenerationFailure('timeout')):
                response=self.request('POST','/qa/translate/stream',json={'text':'Original text','target_language':'zh'})
            events=self.stream_events(response);self.assertEqual(events[-1]['status'],502)
            self.assertIn('响应超时',events[-1]['detail']);self.assertNotIn('translation',events[-1])

        def test_progress_stream_heartbeats_and_disconnect_cancel_transport(self):
            import asyncio, threading, time
            from app.api import generation
            class Request:
                def __init__(self, disconnect=False):self.count=0;self.disconnect=disconnect
                async def is_disconnected(self):
                    self.count+=1
                    return self.disconnect and self.count>2
            closed=threading.Event();finished=threading.Event()
            def hanging(control):
                control.set_abort(closed.set)
                try:
                    control.progress('generating')
                    closed.wait(2)
                    control.checkpoint()
                    return {'complete':True}
                finally:finished.set()
            async def consume(response):
                return [json.loads(line) async for line in response.body_iterator]
            with patch.object(generation,'HEARTBEAT_SECONDS',.01):
                response=generation.generation_response(Request(True),90001,hanging)
                events=asyncio.run(consume(response))
            self.assertTrue(closed.wait(1));self.assertTrue(finished.wait(1))
            self.assertFalse(any(event['event']=='result' for event in events))
            def slow(control):
                control.progress('translating',received_chars=25000);time.sleep(.24);return {'translation':'完整译文'}
            with patch.object(generation,'HEARTBEAT_SECONDS',.01):
                events=asyncio.run(consume(generation.generation_response(Request(),90002,slow)))
            self.assertTrue(any(event['event']=='heartbeat' for event in events))
            self.assertEqual(events[-1],{'event':'result','data':{'translation':'完整译文'}})

        def test_concurrency_rejection_and_thread_start_failure_do_not_leak_slots(self):
            from fastapi import HTTPException
            from app.api import generation
            release=generation.slots.acquire(self.users['alice'])
            try:
                response=self.request('POST','/qa/translate/stream',json={'text':'synthetic','target_language':'zh'})
                self.assertEqual(response.status_code,429)
            finally:release()
            with patch.object(generation.threading.Thread,'start',side_effect=RuntimeError('synthetic thread unavailable')):
                with self.assertRaises(HTTPException) as error:generation.generation_response(None,90003,lambda control:None)
            self.assertEqual(error.exception.status_code,503)
            release=generation.slots.acquire(90003);release()

        def test_legacy_failed_excerpts_are_hidden_on_read_without_rewriting_database(self):
            self.channel()
            with patch.object(qa,'_call_llm_safe',return_value=qa.GenerationFailure('timeout')):result=self.ask().json()
            raw='> 资料总览：本次覆盖 3/34 篇可检索资料的代表性片段。\n\n生成模型本次调用失败：响应超时，未能完成回答。\n\n### [来源1] old.pdf\n\nPRIVATE-RAW-LEGACY-EXCERPT'
            with factory() as db:
                db.get(Message,result['message_id']).content=raw;db.commit()
            response=self.request('GET',f"/qa/sessions/{result['session_id']}/messages")
            self.assertEqual(response.status_code,200,response.text)
            view=response.json()[-1]
            self.assertIn('响应超时',view['content'])
            self.assertIn('覆盖 3/34',view['content'])
            self.assertNotIn('PRIVATE-RAW-LEGACY-EXCERPT',view['content'])
            self.assertNotIn('### [来源',view['content'])
            self.assertTrue(view['citations'])
            with factory() as db:self.assertEqual(db.get(Message,result['message_id']).content,raw)

        def test_new_stream_endpoints_require_real_auth_dependency_without_override(self):
            app.dependency_overrides.pop(get_current_user)
            try:
                with patch.object(qa,'_call_llm_safe') as model:
                    self.assertEqual(client.post('/api/qa/ask/stream',json={'kb_id':self.kb,'question':'hello'}).status_code,401)
                    self.assertEqual(client.post('/api/qa/translate/stream',json={'text':'hello','target_language':'zh'}).status_code,401)
                    model.assert_not_called()
            finally:app.dependency_overrides[get_current_user]=actor

        def test_openapi_has_no_application_question_or_translation_text_max_length(self):
            from app.schemas.qa import QaReq,TranslationReq
            self.assertNotIn('maxLength',QaReq.model_json_schema()['properties']['question'])
            text_schema=TranslationReq.model_json_schema()['properties']['text']
            self.assertNotIn('maxLength',json.dumps(text_schema))

        def test_real_stream_adapter_connects_qa_and_translation_without_network(self):
            from contextlib import contextmanager
            from app.engines import llm_stream
            self.channel();requests=[]
            @contextmanager
            def provider(method,url,**kwargs):
                requests.append(kwargs['json'])
                self.assertTrue(kwargs['json']['stream'])
                self.assertNotIn('max_tokens',kwargs['json'])
                self.assertGreaterEqual(kwargs['timeout'].read,180)
                text='完整综合结论。[来源1]' if '翻译助手' not in kwargs['json']['messages'][0]['content'] else 'Complete translation.'
                content='data: '+json.dumps({'choices':[{'index':0,'delta':{'content':text},'finish_reason':'stop'}]},ensure_ascii=False)+'\n\n'
                response=httpx.Response(200,headers={'content-type':'text/event-stream'},content=content.encode(),request=httpx.Request(method,url))
                try:yield response
                finally:response.close()
            with patch.object(llm_stream.httpx,'stream',side_effect=provider):
                qa_events=self.stream_events(self.request('POST','/qa/ask/stream',json={'kb_id':self.kb,'question':'解释变化检测'}))
                translation=self.stream_events(self.request('POST','/qa/translate/stream',json={'text':'完整翻译。','target_language':'en'}))
            self.assertEqual(qa_events[-1]['data']['answer'],'完整综合结论。[来源1]')
            self.assertEqual(translation[-1]['data']['translation'],'Complete translation.')
            self.assertEqual(len(requests),2)

        def test_stream_adapter_output_limit_never_saves_a_partial_model_answer(self):
            from contextlib import contextmanager
            from app.engines import llm_stream
            self.channel()
            @contextmanager
            def limited(method,url,**kwargs):
                body='data: '+json.dumps({'choices':[{'delta':{'content':'UNFINISHED-MODEL-TEXT [来源1]'},'finish_reason':'length'}]})+'\n\n'
                response=httpx.Response(200,headers={'content-type':'text/event-stream'},content=body.encode(),request=httpx.Request(method,url))
                try:yield response
                finally:response.close()
            with patch.object(llm_stream.httpx,'stream',side_effect=limited):
                response=self.request('POST','/qa/ask/stream',json={'kb_id':self.kb,'question':'解释变化检测'})
            data=self.stream_events(response)[-1]['data']
            self.assertEqual(data['generation_status'],'unavailable')
            self.assertNotIn('UNFINISHED-MODEL-TEXT',response.text)
            self.assertIn('未获得完整回答',data['answer'])
            self.assertNotIn('TAIL-EVIDENCE-9876',data['answer'])
            with factory() as db:self.assertNotIn('UNFINISHED-MODEL-TEXT',db.scalar(select(Message).where(Message.role=='assistant')).content)

    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Contract))
    print('PAPER_QA_RESULT='+json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'synthetic_only':True}))
    client.close(); engine.dispose()
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--fixture':raise SystemExit(fixture(Path(sys.argv[2])))
    unittest.main()