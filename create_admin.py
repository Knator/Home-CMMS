"""Run once to create the initial admin user."""
from app import create_app
from app.extensions import db
from app.models.user import User

app = create_app()

with app.app_context():
    username = input("Admin username: ").strip()
    email = input("Admin email: ").strip()
    password = input("Admin password (min 8 chars): ").strip()

    if User.query.filter_by(username=username).first():
        print("Username already exists.")
    else:
        user = User(username=username, email=email, role='admin')
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"Admin user '{username}' created.")
