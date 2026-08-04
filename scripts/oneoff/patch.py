import codecs

path = 'app/domains/admin/router.py'
with codecs.open(path, 'r', 'utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if 'grade_str = "S급(MINT)"' in line:
        lines[i] = line + '        suggested_grade = "MINT"\n        suggested_decision = "APPROVE_NORMAL"\n'
    elif 'grade_str = "A급(GOOD)"' in line:
        lines[i] = line + '        suggested_grade = "GOOD"\n        suggested_decision = "APPROVE_NORMAL"\n'
    elif 'grade_str = "GOOD B급(NORMAL)"' in line:
        lines[i] = line + '        suggested_grade = "NORMAL"\n        suggested_decision = "APPROVE_DOWNGRADE"\n'
    elif 'grade_str = "REJECT C급(폐기/반려)"' in line:
        lines[i] = line + '        suggested_grade = "REJECT"\n        suggested_decision = "REJECT_RETURN"\n'
    elif 'existing_logs["summary"] = "[Vision Agent CLAHE] AI 비전 재검수 완료 (지연됨)"' in line:
        lines[i] = line + '    existing_logs["suggested_grade"] = suggested_grade\n    existing_logs["suggested_decision"] = suggested_decision\n'

with codecs.open(path, 'w', 'utf-8') as f:
    f.writelines(lines)
