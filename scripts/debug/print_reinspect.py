import codecs
path = 'app/domains/admin/router.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

idx = content.find('def trigger_ai_reinspection')
print(content[idx+5000:idx+6000])
