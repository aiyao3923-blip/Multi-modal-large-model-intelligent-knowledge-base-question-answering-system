"""Read-only startup preflight; never imports app or initializes settings/database.

``ok`` is the launcher's ready boolean (warnings do not block); exits are 0/2.
Unknown paths/counts are null. Requirement issue lists contain strings; upload
issue lists contain document IDs only. Paths follow Settings' backend-relative
semantics, except --config, which is relative to the project root.

The 0001 contract/checksum matches db/migrations; legacy needs no migration.
Unlike the service's empty-table fallback, untracked/partial version DDL fails
closed. Immutable mode prevents SQLite sidecar writes; WAL/journal databases
must be quiescent and have no recovery sidecars before this offline preflight.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import closing
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import sqlite3
import sys

# Third-party imports must not create __pycache__ during a read-only CLI run.
sys.dont_write_bytecode = True
BACKEND_DIR = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    """Safe configuration error; never contains parser or input values."""


class CheckError(ValueError):
    """A safe, value-free explanation of an inconsistent database."""


def _requirements(path: Path, report: dict) -> None:
    result = report['requirements']
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError):
        report['errors'].append('无法读取 backend/requirements.txt。')
        return
    try:
        from packaging.requirements import Requirement
    except ImportError:
        result['missing'].append('packaging')
        report['errors'].append('缺少 packaging，无法校验依赖约束；仍继续其他只读检查。')
        return
    for number, line in enumerate(lines, 1):
        line = re.split(r'\s+#', line.strip(), maxsplit=1)[0]
        if not line or line.startswith('#'):
            continue
        try:
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
        except (ValueError, TypeError):
            report['errors'].append(f'requirements.txt 第 {number} 行不是支持的顶层约束。')
            continue
        name = requirement.name
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            result['missing'].append(name)
            continue
        except (OSError, ValueError):
            report['errors'].append(f'无法读取依赖元数据：{name}。')
            continue
        result['versions'][name] = installed
        try:
            compatible = not requirement.url and requirement.specifier.contains(installed)
        except ValueError:
            compatible = False
        if not compatible:
            # Never echo a direct URL (it could contain credentials).
            result['incompatible'].append(f'{name}=={installed} (要求 {requirement.specifier or "可验证版本约束"})')
    result['missing'] = sorted(set(result['missing']), key=str.casefold)
    result['incompatible'].sort()
    if result['missing']:
        report['errors'].append('缺少必需依赖：' + ', '.join(result['missing']) + '。')
    if result['incompatible']:
        report['errors'].append('已安装依赖不满足 requirements 顶层约束。')


def _storage(config_path: Path, backend: Path, report: dict) -> tuple[Path, Path]:
    if not config_path.is_file():
        raise ConfigError('配置文件缺失或不是普通文件；未创建配置或数据库。')
    if not config_path.is_relative_to(backend.parent):
        report['warnings'].append('配置文件位于项目根外；仅按显式路径读取，不修改。')
    try:
        import yaml
    except ImportError:
        if 'PyYAML' not in report['requirements']['missing']:
            report['requirements']['missing'].append('PyYAML')
        raise ConfigError('缺少 PyYAML，无法读取配置。') from None
    try:
        data = yaml.safe_load(config_path.read_text(encoding='utf-8-sig')) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        # YAML exceptions can include entire secret-bearing lines; do not log them.
        raise ConfigError('配置无法读取或 YAML 语法无效（内容已隐藏）。') from None
    storage = data.get('storage', {}) if isinstance(data, dict) else None
    if not isinstance(storage, dict):
        raise ConfigError('配置根节点及 storage 必须为映射（内容已隐藏）。')
    paths = {key: storage.get(key, default) for key, default in (
        ('data_dir', 'storage/data'), ('db_name', 'app.db'), ('upload_dir', 'storage/uploads'))}
    if any(not isinstance(value, str) or not value.strip() or '\0' in value for value in paths.values()):
        raise ConfigError('storage 路径必须为非空字符串（内容已隐藏）。')
    database = (backend / paths['data_dir'] / paths['db_name']).resolve()
    uploads = (backend / paths['upload_dir']).resolve()
    if not database.is_relative_to(backend):
        report['warnings'].append('数据库位于 backend 根外；仅检查，不迁移。')
    if not uploads.is_relative_to(backend):
        report['warnings'].append('上传目录位于 backend 根外；仅核验引用路径，不扫描或修复。')
    return database, uploads


def _normalize_sql(sql: str) -> str:
    sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
    return re.sub(r'[\s";]', '', sql).casefold().replace('ifnotexists', '')


def _schema_contract(backend: Path) -> tuple[dict, str, str, dict]:
    """Read trusted source as data; no SQLAlchemy, Settings or app imports."""
    tree = ast.parse((backend / 'app/db/models/entity.py').read_text(encoding='utf-8-sig'))
    legacy = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        table = next((ast.literal_eval(item.value) for item in node.body
                      if isinstance(item, ast.Assign) and any(
                          isinstance(target, ast.Name) and target.id == '__tablename__' for target in item.targets)), None)
        if table:
            legacy[table] = {item.target.id for item in node.body
                             if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
                             and isinstance(item.value, ast.Call) and isinstance(item.value.func, ast.Name)
                             and item.value.func.id == 'mapped_column'}
    migration_dir = backend / 'app/db/migrations'
    tree = ast.parse((migration_dir / '__init__.py').read_text(encoding='utf-8-sig'))
    version = next((ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == 'VERSION' for target in node.targets)), None)
    if version != '0001_document_versions' or not {'id', 'storage_path'} <= legacy.get('documents', set()):
        raise ValueError('unsupported local schema contract')
    sql_bytes = (migration_dir / (version + '.sql')).read_bytes()
    checksum = hashlib.sha256(sql_bytes + b'\x00legacy-backfill-v1').hexdigest()
    definitions, buffer = {}, ''
    for line in sql_bytes.decode('utf-8-sig').splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            match = re.search(r'CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+(\w+)', buffer, re.I)
            if not match:
                raise ValueError('unsupported migration statement')
            definitions[(match[1].lower(), match[2])] = _normalize_sql(buffer)
            buffer = ''
    if _normalize_sql(buffer) or ('table', 'schema_migrations') not in definitions:
        raise ValueError('incomplete migration contract')
    return legacy, version, checksum, definitions


def _schema_state(connection: sqlite3.Connection, contract: tuple) -> str:
    legacy, version, checksum, definitions = contract
    actual = {(kind, name): sql for kind, name, sql in connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE type IN ('table','index','trigger')")}
    tables = {name for kind, name in actual if kind == 'table'}
    missing = legacy.keys() - tables
    if missing:
        raise CheckError('缺少业务表：' + ', '.join(sorted(missing)) + '；禁止自动建表。')
    for table, expected in legacy.items():
        columns = {row[1] for row in connection.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')}
        if not expected <= columns:
            raise CheckError(f'业务表 {table} 缺少必需列；禁止自动补表。')
    version_tables = {name for kind, name in definitions if kind == 'table'}
    present = version_tables & tables
    if not present:
        if definitions.keys() & actual.keys():
            raise CheckError('存在未跟踪的版本索引或触发器；禁止回退旧模式。')
        return 'legacy'
    if present != version_tables:
        raise CheckError('检测到部分版本表；请核对迁移，禁止启动或自动修复。')
    for key, expected in definitions.items():
        if key not in actual or not actual[key] or _normalize_sql(actual[key]) != expected:
            raise CheckError(f'版本结构缺失或不符：{key[0]} {key[1]}。')
    records = connection.execute('SELECT version, checksum FROM schema_migrations ORDER BY version LIMIT 2').fetchall()
    if records != [(version, checksum)]:
        raise CheckError('版本迁移记录缺失、未知或校验值不符；禁止启动或自动迁移。')
    return 'versioned'


def _uploads(connection: sqlite3.Connection, backend: Path, uploads: Path, report: dict) -> None:
    result = report['database']
    result['document_count'] = 0
    for document_id, stored in connection.execute('SELECT id, storage_path FROM documents ORDER BY id'):
        if not isinstance(document_id, int):
            raise CheckError('documents.id 类型无效；未输出异常行内容。')
        result['document_count'] += 1
        try:
            if not isinstance(stored, str) or not stored.strip() or '\0' in stored:
                result['missing_upload_ids'].append(document_id)
                continue
            target = (backend / stored).resolve()
            if not target.is_relative_to(uploads):
                result['outside_upload_ids'].append(document_id)
            elif not target.is_file():
                result['missing_upload_ids'].append(document_id)
        except (OSError, ValueError, RuntimeError):
            result['missing_upload_ids'].append(document_id)
    for key, label in (('missing_upload_ids', '上传件缺失或路径不可读取'), ('outside_upload_ids', '资源越出配置上传目录')):
        if result[key]:
            report['warnings'].append(f'{label}：{len(result[key])} 个文档；仅报告 ID，未修复。')


def _database(path: Path, uploads: Path, backend: Path, report: dict) -> None:
    result = report['database']
    result['path'], result['exists'] = str(path), path.exists()
    if not result['exists']:
        result['state'] = 'missing'
        report['errors'].append('数据库不存在；只读诊断不会创建数据库，禁止启动。')
        return
    if not path.is_file():
        report['errors'].append('数据库路径不是普通文件。')
        return
    # mode=ro alone can still create/write WAL shared-memory files. Never ignore
    # an existing journal with immutable=1, which would hide committed WAL data.
    if any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        report['errors'].append('存在 SQLite WAL/日志辅助文件；本检查不恢复或写辅助文件，请停库核对后重试。')
        return
    try:
        contract = _schema_contract(backend)
    except (OSError, UnicodeError, ValueError, SyntaxError, TypeError):
        report['errors'].append('无法读取或不支持本地源码的表结构/迁移约定；未猜测数据库版本。')
        return
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=3)) as connection:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise CheckError('SQLite quick_check 未通过；未执行修复。')
            result['state'] = _schema_state(connection, contract)
            _uploads(connection, backend, uploads, report)
    except CheckError as error:
        result['state'] = 'inconsistent'
        report['errors'].append(str(error))
    except (sqlite3.Error, OSError, ValueError):
        result['state'] = 'unknown'
        report['errors'].append('SQLite 只读检查失败（无法读取、锁定或损坏）；未输出库内容或执行修复。')


def diagnose(config: str | None = None, *, backend: Path = BACKEND_DIR) -> dict:
    report = {
        'ok': False,
        'python': {'executable': sys.executable, 'version': sys.version.split()[0]},
        'requirements': {'missing': [], 'incompatible': [], 'versions': {}},
        'config': {'path': None},
        'database': {'path': None, 'exists': False, 'state': 'unknown', 'document_count': None,
                     'missing_upload_ids': [], 'outside_upload_ids': []},
        'warnings': [], 'errors': [],
    }
    backend = backend.resolve()
    _requirements(backend / 'requirements.txt', report)
    try:
        config_path = (backend.parent / config if config is not None else backend / 'config.yaml').resolve()
        report['config']['path'] = str(config_path)
        database, uploads = _storage(config_path, backend, report)
    except ConfigError as error:
        report['errors'].append(str(error))
    except (OSError, ValueError, RuntimeError):
        report['errors'].append('无法解析配置或存储路径（内容已隐藏）。')
    else:
        try:
            _database(database, uploads, backend, report)
        except (OSError, ValueError, RuntimeError):
            report['errors'].append('无法检查数据库路径（内容已隐藏）。')
    report['ok'] = not report['errors'] and report['database']['state'] in ('legacy', 'versioned')
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='只读本机后端诊断；不会建库、迁移或修复资源。')
    parser.add_argument('--config', help='配置路径；相对路径按项目根解释，默认 backend/config.yaml')
    parser.add_argument('--json', action='store_true', help='输出稳定 JSON 协议；ok 即启动就绪真值')
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    report = diagnose(args.config)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print('本机后端诊断：' + ('可启动' if report['ok'] else '检查不通过，禁止启动'))
        print(f"Python：{report['python']['version']} | {report['python']['executable']}")
        print(f"配置：{report['config']['path']}")
        print(f"数据库：{report['database']['state']} | {report['database']['path']}")
        print(f"文档数：{report['database']['document_count']}")
        for level, label in (('warnings', '警告'), ('errors', '错误')):
            for message in report[level]:
                print(f'{label}：{message}')
    return 0 if report['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
