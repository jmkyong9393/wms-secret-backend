import codecs
path = 'app/domains/admin/router.py'
with codecs.open(path, 'r', 'utf-8') as f:
    content = f.read()

new_content = content.replace('job.agent_logs["defect_coordinates"] = final_state.get("defects", [])', 'job.agent_logs["defect_coordinates"] = final_state.get("defects", [])\n        from sqlalchemy.orm.attributes import flag_modified\n        flag_modified(job, "agent_logs")')

with codecs.open(path, 'w', 'utf-8') as f:
    f.write(new_content)
