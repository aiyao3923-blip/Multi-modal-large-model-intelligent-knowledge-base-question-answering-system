"""Build only a synthetic, isolated UI fixture. Never loads production config/DB."""
from pathlib import Path
import argparse
import json
import os
import sys

PROJECT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True, help='New directory under project storage; never production data')
parser.add_argument('--model-dir', type=Path, help='Optional existing local ONNX E5 model; no download')
args = parser.parse_args()
allowed = (PROJECT / 'storage' / 'refactor-validation').resolve()
ROOT = args.output.resolve()
if ROOT == allowed or not ROOT.is_relative_to(allowed):
    parser.error('Output must be a new child of storage/refactor-validation')

if ROOT.exists():
    raise SystemExit('Fixture already exists; inspect, do not reinitialize')
ROOT.mkdir(parents=True)
model = args.model_dir.resolve(strict=True) if args.model_dir else None
config = ROOT / 'fixture.yaml'
config.write_text(json.dumps({
    'app': {'name': '论文工作区 · 隔离验收', 'version': 'refactor-test'},
    'storage': {'data_dir': str(ROOT/'data'), 'upload_dir': str(ROOT/'uploads'), 'vector_dir': str(ROOT/'vectors')},
    'security': {'secret_key': 'synthetic-ui-only-never-production', 'pbkdf2_iterations': 1000},
    'embedding': {'backend': 'onnx_e5', 'model_path': str(model), 'threads': 4, 'batch_size': 8} if model else {},
}), encoding='utf-8')
for key in tuple(os.environ):
    if key.startswith('APP_'): os.environ.pop(key)
os.environ['APP_CONFIG'] = str(config)
os.environ['HF_HUB_OFFLINE'] = '1'
sys.path.insert(0, str(PROJECT/'backend'))
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import Base, get_engine, get_session_factory
from app.db.models import Role, User, KnowledgeBase, Document, Chunk, VERSIONING_TABLE_NAMES
from app.engines.doc_ingest import ingest_document
from app.services.kb_service import create_kb, save_upload, create_parse_task
from app.schemas.kb import KbCreate
from sqlalchemy import select, func
assert settings.db_path.resolve().is_relative_to(ROOT)
settings.ensure_dirs()
engine = get_engine()
Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in VERSIONING_TABLE_NAMES])
factory=get_session_factory()
with factory() as db:
    db.add_all([Role(code=r, name=r) for r in ('admin','operator','user')])
    salt,digest=hash_password('SyntheticOnly2026!')
    alice=User(username='reader_test',display_name='论文阅读验收',role_code='user',password_salt=salt,password_digest=digest,is_active=True)
    bob=User(username='other_test',display_name='隔离对照用户',role_code='user',password_salt=salt,password_digest=digest,is_active=True)
    db.add_all([alice,bob]); db.commit()
    lib=create_kb(db,alice,KbCreate(name='遥感变化检测 · 阅读',description='合成论文，用于本地浏览器验收'))
    second=create_kb(db,alice,KbCreate(name='课程笔记',description='独立作用域'))
    private=create_kb(db,bob,KbCreate(name='另一用户的私有资料',description='不得出现在阅读者的目录'))
    corpus=[
      ('01_多尺度特征融合与变化检测.md', '# 多尺度特征融合的变化检测方法\n\n## 研究问题\n该合成论文研究不同时相遥感影像中的地物变化检测，重点是矿区边界破碎和小目标遗漏。\n\n## 方法\n模型使用共享编码器提取双时相特征。在解码阶段将低层纹理与高层语义进行多尺度融合，通过差分特征定位变化区域。与简单像素差分相比，这样的方法能够抑制光照和季节引起的伪变化。\n\n## 实验设计\n训练集与测试集按区域划分，不让同一区域的重叠切片跨越两者。评价采用精确率、召回率、F1和IoU；该合成资料没有给出真实数值，不能据此宣称提升。\n\n## 局限\n空间配准误差会导致边界假阳性。建议独立报告边界区域表现，并检查相邻地区的泛化。'),
      ('02_跨时相注意力与Transformer.md', '# 跨时相注意力\n\n## 核心贡献\n该合成研究通过跨时相注意力显式对齐前后影像的语义特征，用Transformer建模长距离依赖。它把不同时相的token作为互相关联的信息，而非独立分类。\n\n## 与卷积方法比较\n跨时相注意力能利用较大空间范围的上下文，但增加计算和显存开销。多尺度卷积更强调局部纹理和边界，通常便于部署；两类方法可能互补。\n\n## 评价边界\n必须在相同数据划分、分辨率、训练预算下进行比较。没有公平对照实验，不应只根据模型名称判断哪个更好。该内容为验收合成资料，不是实际学术结论。'),
      ('03_弱监督学习与研究展望.md', '# 弱监督变化检测\n\n## 标注成本\n矿区逐像素标签需要专业人员逐幅检查。弱监督方法尝试从图像级标签或少量点标注学习，降低标注需求，但定位精度仍依赖训练信号。\n\n## 可能的研究方向\n可联合使用多尺度边界特征、跨时相注意力和不确定性估计，让模型把不确定区域交由人工复核。需要通过消融实验分别验证每个模块，而不是简单堆叠。\n\n## 局限\n合成说明不包含真实实验数据；只能用于测试检索、来源绑定和页面渲染。'),
    ]
    docs=[]
    for name, text in corpus:
        doc,_=save_upload(db,lib,alice,name,text.encode('utf-8')); ingest_document(create_parse_task(db,doc,alice).id); docs.append(doc.id)
    doc,_=save_upload(db,second,alice,'课程说明.md','课程笔记的独立标记是 ORBIT-COURSE-ONLY。与遥感论文内容无关。'.encode()); ingest_document(create_parse_task(db,doc,alice).id)
    doc,_=save_upload(db,private,bob,'绝不跨用户出现.md','PRIVATE-BOB-ONLY 这个标记不得出现在阅读者问答中。'.encode()); ingest_document(create_parse_task(db,doc,bob).id)
    db.commit()
    summary={'root':str(ROOT),'config':str(config),'kb_id':lib.id,'second_kb_id':second.id,'private_kb_id':private.id,'document_ids':docs,'vector_chunks':db.scalar(select(func.count()).select_from(Chunk).where(Chunk.vector_json.is_not(None))),'fixture_only':True}
(ROOT/'fixture-receipt.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))