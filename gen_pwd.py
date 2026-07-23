import bcrypt

print("1234:", bcrypt.hashpw(b'1234', bcrypt.gensalt()).decode('utf-8'))
print("password:", bcrypt.hashpw(b'password', bcrypt.gensalt()).decode('utf-8'))
