import os
import json
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, g
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY')

database_url = os.getenv('DATABASE_URL', 'sqlite:///database.db')
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
client = genai.Client()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_banned = db.Column(db.Boolean, default=False)
    project_limit = db.Column(db.Integer, default=3)
    projects = db.relationship('Project', backref='author', cascade='all, delete-orphan', lazy=True)
    votes = db.relationship('Vote', backref='voter', cascade='all, delete-orphan', lazy=True)
    comments = db.relationship('Comment', backref='author', cascade='all, delete-orphan', lazy=True)
    bookmarks = db.relationship('Bookmark', backref='user', cascade='all, delete-orphan', lazy=True)

class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    tech_stack = db.Column(db.String(200), nullable=False)
    stage = db.Column(db.String(50), nullable=False)
    elevator_pitch = db.Column(db.Text, nullable=False)
    industry_tag = db.Column(db.String(50), nullable=False)
    monetization_paths = db.Column(db.Text, nullable=False)
    target_users = db.Column(db.Text, nullable=False)
    pricing_idea = db.Column(db.Text, nullable=False)
    competitors = db.Column(db.Text, nullable=False)
    votes = db.relationship('Vote', backref='project', cascade='all, delete-orphan', lazy=True)
    comments = db.relationship('Comment', backref='project', cascade='all, delete-orphan', lazy=True)
    bookmarks = db.relationship('Bookmark', backref='project', cascade='all, delete-orphan', lazy=True)

class Vote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)

class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)

with app.app_context():
    db.create_all()
    admin_user = User.query.filter_by(username='admin').first()
    if not admin_user:
        admin_user = User(username='admin', password_hash=generate_password_hash('admin123'), is_admin=True, project_limit=0)
        db.session.add(admin_user)
        db.session.commit()

@app.before_request
def load_user():
    g.user = None
    g.unread_count = 0
    if 'user_id' in session:
        g.user = User.query.get(session['user_id'])
        if g.user and not g.user.is_admin:
            g.unread_count = Comment.query.join(Project).filter(
                Project.user_id == g.user.id, 
                Comment.is_read == False, 
                Comment.user_id != g.user.id
            ).count()
    
    allowed_routes = ['login', 'register', 'static', 'pitch', 'index']
    if not g.user and request.endpoint not in allowed_routes:
        return redirect(url_for('login'))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.user or not g.user.is_admin:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username.lower() == 'admin':
            error = "This username is reserved."
        elif User.query.filter_by(username=username).first():
            error = "Username already exists."
        else:
            new_user = User(username=username, password_hash=generate_password_hash(password))
            db.session.add(new_user)
            db.session.commit()
            session['user_id'] = new_user.id
            return redirect(url_for('index'))
    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            if user.is_banned:
                error = "This account has been banned by an administrator."
            else:
                session['user_id'] = user.id
                if user.is_admin:
                    return redirect(url_for('admin_dashboard'))
                return redirect(url_for('index'))
        else:
            error = "Invalid username or password."
    return render_template('login.html', error=error)

@app.route('/', methods=['GET', 'POST'])
def index():
    if g.user and g.user.is_admin:
        return redirect(url_for('admin_dashboard'))

    error = None
    form_data = {}
    if request.method == 'POST':
        if not g.user:
            return redirect(url_for('login'))

        title = request.form.get('title')
        description = request.form.get('description')
        tech_stack = request.form.get('tech_stack')
        stage = request.form.get('stage')
        form_data = {'title': title, 'description': description, 'tech_stack': tech_stack, 'stage': stage}
        
        project_count = Project.query.filter_by(user_id=g.user.id).count()
        if project_count >= g.user.project_limit:
            error = f"Limit reached! You can only generate up to {g.user.project_limit} projects."
            return render_template('index.html', error=error, form_data=form_data)

        prompt = f"""
        Analyze this project and provide a business strategy.
        Title: {title}
        Description: {description}
        Tech Stack: {tech_stack}
        Stage: {stage}
        
        Return a JSON object with EXACTLY these keys:
        - "elevator_pitch": A punchy 2-sentence marketing summary of the product.
        - "industry_tag": A single 1-3 word category (e.g., FinTech, EdTech, SaaS).
        - "monetization_paths": A JSON array of 3 specific string suggestions.
        - "target_users": A JSON array of 2 specific string target demographics.
        - "pricing_idea": A single string explaining a clear pricing structure.
        - "competitors": A JSON array of 2 specific real-world companies doing something similar.
        """
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            data = json.loads(response.text)
            new_project = Project(
                user_id=g.user.id,
                title=title,
                description=description,
                tech_stack=tech_stack,
                stage=stage,
                elevator_pitch=data.get('elevator_pitch', ''),
                industry_tag=data.get('industry_tag', 'Technology'),
                monetization_paths=json.dumps(data.get('monetization_paths', [])),
                target_users=json.dumps(data.get('target_users', [])),
                pricing_idea=data.get('pricing_idea', ''),
                competitors=json.dumps(data.get('competitors', []))
            )
            db.session.add(new_project)
            db.session.commit()
            return redirect(url_for('dashboard', project_id=new_project.id))
        except APIError as e:
            error_msg = str(e).lower()
            if '429' in error_msg or 'quota' in error_msg:
                error = "API Quota exhausted. Please try again later."
            elif '503' in error_msg or 'unavailable' in error_msg:
                error = "Server is extremely busy. Please wait a minute and try again."
            else:
                error = "An error occurred with the AI Engine. Please check your inputs."
            return render_template('index.html', error=error, form_data=form_data)
        except Exception:
            error = "An unexpected system error occurred. Please try again."
            return render_template('index.html', error=error, form_data=form_data)

    return render_template('index.html', error=error, form_data=form_data)

@app.route('/dashboard/<int:project_id>', methods=['GET', 'POST'])
def dashboard(project_id):
    project = Project.query.get_or_404(project_id)
    
    if g.user and g.user.id == project.user_id:
        unread = Comment.query.filter_by(project_id=project.id, is_read=False).all()
        for c in unread:
            if c.user_id != g.user.id:
                c.is_read = True
        db.session.commit()

    if request.method == 'POST':
        if g.user.is_admin:
            return redirect(url_for('dashboard', project_id=project.id))
        text = request.form.get('text')
        if text:
            new_comment = Comment(user_id=g.user.id, project_id=project_id, text=text)
            db.session.add(new_comment)
            db.session.commit()
        return redirect(url_for('dashboard', project_id=project.id))
        
    paths = json.loads(project.monetization_paths)
    users = json.loads(project.target_users)
    competitors = json.loads(project.competitors)
    comments = Comment.query.filter_by(project_id=project_id).order_by(Comment.created_at.desc()).all()
    return render_template('dashboard.html', project=project, paths=paths, users=users, competitors=competitors, comments=comments)

@app.route('/pitch/<int:project_id>')
def pitch(project_id):
    project = Project.query.get_or_404(project_id)
    paths = json.loads(project.monetization_paths)
    users = json.loads(project.target_users)
    competitors = json.loads(project.competitors)
    return render_template('pitch.html', project=project, paths=paths, users=users, competitors=competitors)

@app.route('/feed')
def feed():
    q = request.args.get('q', '')
    stage_filter = request.args.get('stage', '')
    
    query = Project.query
    if q:
        query = query.filter(or_(Project.title.ilike(f'%{q}%'), Project.industry_tag.ilike(f'%{q}%')))
    if stage_filter:
        query = query.filter_by(stage=stage_filter)
        
    projects = query.all()
    projects_data = []
    
    for p in projects:
        vote_count = Vote.query.filter_by(project_id=p.id).count()
        has_voted = False
        has_bookmarked = False
        if g.user and not g.user.is_admin:
            has_voted = Vote.query.filter_by(project_id=p.id, user_id=g.user.id).first() is not None
            has_bookmarked = Bookmark.query.filter_by(project_id=p.id, user_id=g.user.id).first() is not None
        projects_data.append({'project': p, 'votes': vote_count, 'has_voted': has_voted, 'has_bookmarked': has_bookmarked})
    
    projects_data.sort(key=lambda x: x['votes'], reverse=True)
    return render_template('feed.html', projects_data=projects_data)

@app.route('/vote/<int:project_id>', methods=['POST'])
def vote(project_id):
    if g.user.is_admin:
        return redirect(url_for('feed'))
    existing_vote = Vote.query.filter_by(user_id=g.user.id, project_id=project_id).first()
    if existing_vote:
        db.session.delete(existing_vote)
    else:
        new_vote = Vote(user_id=g.user.id, project_id=project_id)
        db.session.add(new_vote)
    db.session.commit()
    return redirect(request.referrer or url_for('feed'))

@app.route('/bookmark/<int:project_id>', methods=['POST'])
def bookmark(project_id):
    if g.user.is_admin:
        return redirect(url_for('feed'))
    existing = Bookmark.query.filter_by(user_id=g.user.id, project_id=project_id).first()
    if existing:
        db.session.delete(existing)
    else:
        db.session.add(Bookmark(user_id=g.user.id, project_id=project_id))
    db.session.commit()
    return redirect(request.referrer or url_for('feed'))

@app.route('/profile')
def profile():
    if g.user.is_admin:
        return redirect(url_for('admin_dashboard'))
    projects = Project.query.filter_by(user_id=g.user.id).all()
    bookmarks = Bookmark.query.filter_by(user_id=g.user.id).all()
    bookmarked_projects = [b.project for b in bookmarks]
    return render_template('profile.html', projects=projects, bookmarked_projects=bookmarked_projects)

@app.route('/edit/<int:project_id>', methods=['GET', 'POST'])
def edit(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id != g.user.id and not g.user.is_admin:
        return redirect(url_for('index'))
    if request.method == 'POST':
        project.title = request.form.get('title')
        project.description = request.form.get('description')
        project.tech_stack = request.form.get('tech_stack')
        project.stage = request.form.get('stage')
        db.session.commit()
        return redirect(url_for('dashboard', project_id=project.id))
    return render_template('edit.html', project=project)

@app.route('/delete/<int:project_id>', methods=['POST'])
def delete(project_id):
    project = Project.query.get_or_404(project_id)
    if project.user_id == g.user.id or g.user.is_admin:
        db.session.delete(project)
        db.session.commit()
    return redirect(request.referrer or url_for('profile'))

@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin_dashboard():
    if request.method == 'POST':
        new_global_limit = request.form.get('global_limit')
        if new_global_limit and new_global_limit.isdigit():
            users = User.query.filter_by(is_admin=False).all()
            for u in users:
                u.project_limit = int(new_global_limit)
            db.session.commit()
            return redirect(url_for('admin_dashboard'))
    users = User.query.filter_by(is_admin=False).all()
    projects = Project.query.all()
    votes_count = Vote.query.count()
    comments_count = Comment.query.count()
    return render_template('admin.html', users=users, projects=projects, votes_count=votes_count, comments_count=comments_count)

@app.route('/admin/user/<int:user_id>', methods=['GET', 'POST'])
@admin_required
def admin_user(user_id):
    target_user = User.query.get_or_404(user_id)
    if target_user.is_admin:
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'update_limit':
            new_limit = request.form.get('limit')
            if new_limit and new_limit.isdigit():
                target_user.project_limit = int(new_limit)
                db.session.commit()
        elif action == 'toggle_ban':
            target_user.is_banned = not target_user.is_banned
            db.session.commit()
        elif action == 'delete_user':
            db.session.delete(target_user)
            db.session.commit()
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('admin_user', user_id=target_user.id))
    return render_template('admin_user.html', target_user=target_user)

@app.route('/admin/delete_comment/<int:comment_id>', methods=['POST'])
@admin_required
def admin_delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    db.session.delete(comment)
    db.session.commit()
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)