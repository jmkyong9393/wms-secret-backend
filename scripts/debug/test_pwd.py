import bcrypt

hash_val = b'$2b$12$yreXiYCNzDKSd522qPQUGuTxuQjVut3zalgQgpJKMNTlcLHlACjnC'
for p in [b'1234', b'password', b'admin', b'123456', b'admin123', b'admin123!', b'1111', b'0000', b'EMP0001']:
    if bcrypt.checkpw(p, hash_val):
        print('FOUND PASSWORD:', p.decode('utf-8'))
        break
print('Done.')
