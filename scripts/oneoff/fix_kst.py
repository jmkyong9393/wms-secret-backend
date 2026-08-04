import os, glob

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original = content
    # Replace datetime.utcnow() with now_kst()
    content = content.replace('datetime.utcnow()', 'now_kst()')
    # Replace datetime.datetime.utcnow() with now_kst()
    content = content.replace('datetime.datetime.utcnow()', 'now_kst()')
    # For datetime.datetime.now() (without args)
    content = content.replace('datetime.datetime.now()', 'now_kst()')

    if content != original:
        lines = content.split('\n')
        import_idx = 0
        has_now_kst_import = False
        for i, line in enumerate(lines):
            if 'import now_kst' in line:
                has_now_kst_import = True
            if line.startswith('import ') or line.startswith('from '):
                import_idx = i
        
        if not has_now_kst_import:
            lines.insert(import_idx + 1, 'from app.models.wms import now_kst')
        
        content = '\n'.join(lines)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Fixed {filepath}")

for filepath in glob.glob('app/**/*.py', recursive=True):
    if 'models\\\\wms.py' in filepath or 'models/wms.py' in filepath:
        continue
    fix_file(filepath)
