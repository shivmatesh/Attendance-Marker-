from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import io
import csv

app = Flask(__name__)
app.config['SECRET_KEY'] = 'attendance-tracker-secret-key-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///attendance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'


# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='member')  # 'admin' or 'member'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    attendance_records = db.relationship('Attendance', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    status = db.Column(db.String(20), nullable=False)  # 'present' or 'absent'
    marked_by = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint('user_id', 'date', name='unique_user_date'),)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            login_user(user)
            flash('Login successful!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    if current_user.role != 'admin':
        flash('Only admins can register new users', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role', 'member')

        if User.query.filter_by(username=username).first():
            flash('Username already exists', 'error')
        else:
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'User {username} registered successfully!', 'success')
            return redirect(url_for('manage_users'))

    return render_template('register.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    today = datetime.now().date()
    start_of_month = today.replace(day=1)

    # Today's attendance for current user
    today_attendance = Attendance.query.filter_by(
        user_id=current_user.id,
        date=today
    ).first()

    # This month's attendance stats
    month_records = Attendance.query.filter(
        Attendance.user_id == current_user.id,
        Attendance.date >= start_of_month
    ).all()

    present_count = sum(1 for r in month_records if r.status == 'present')
    total_days = len(month_records)
    attendance_percentage = round((present_count / total_days * 100), 1) if total_days > 0 else 0

    # Recent attendance records (all users)
    recent_attendance = Attendance.query.order_by(Attendance.date.desc(), Attendance.created_at.desc()).limit(10).all()

    return render_template('dashboard.html',
                         today=today,
                         today_attendance=today_attendance,
                         present_count=present_count,
                         total_days=total_days,
                         attendance_percentage=attendance_percentage,
                         recent_attendance=recent_attendance)


@app.route('/mark-attendance', methods=['GET', 'POST'])
@login_required
def mark_attendance():
    users = User.query.order_by(User.username).all()
    today = datetime.now().date()

    if request.method == 'POST':
        user_ids = request.form.getlist('user_ids')
        status = request.form.get('status')

        if not user_ids:
            flash('Please select at least one user', 'error')
        else:
            marked_count = 0
            for user_id in user_ids:
                # Only admins can mark for others, members can only mark themselves
                if current_user.role != 'admin' and str(user_id) != str(current_user.id):
                    continue

                existing = Attendance.query.filter_by(user_id=user_id, date=today).first()
                if existing:
                    existing.status = status
                else:
                    attendance = Attendance(
                        user_id=user_id,
                        date=today,
                        status=status,
                        marked_by=current_user.username
                    )
                    db.session.add(attendance)
                marked_count += 1

            db.session.commit()
            flash(f'Attendance marked for {marked_count} user(s)!', 'success')
            return redirect(url_for('dashboard'))

    return render_template('mark_attendance.html', users=users, today=today)


@app.route('/mark-all-present', methods=['POST'])
@login_required
def mark_all_present():
    if current_user.role != 'admin':
        flash('Only admins can mark attendance', 'error')
        return redirect(url_for('dashboard'))

    today = datetime.now().date()
    users = User.query.all()

    for user in users:
        existing = Attendance.query.filter_by(user_id=user.id, date=today).first()
        if not existing:
            attendance = Attendance(
                user_id=user.id,
                date=today,
                status='present',
                marked_by=current_user.username
            )
            db.session.add(attendance)

    db.session.commit()
    flash(f'Marked all {len(users)} users as present!', 'success')
    return redirect(url_for('dashboard'))


@app.route('/reports')
@login_required
def reports():
    users = User.query.order_by(User.username).all()

    # Get filter parameters
    selected_user_id = request.args.get('user_id', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    # Set default dates (this month)
    today = datetime.now().date()
    if not start_date:
        start_date = today.replace(day=1).isoformat()
    if not end_date:
        end_date = today.isoformat()

    start_date_obj = datetime.fromisoformat(start_date)
    end_date_obj = datetime.fromisoformat(end_date)

    # Build query filters
    filters = [Attendance.date >= start_date_obj, Attendance.date <= end_date_obj]
    if selected_user_id:
        filters.append(Attendance.user_id == int(selected_user_id))

    # Get detailed records
    records = Attendance.query.filter(*filters).order_by(Attendance.date.desc()).all()

    # Calculate summary data per user
    user_ids = [u.id for u in users] if not selected_user_id else [int(selected_user_id)]
    attendance_data = []

    for user_id in user_ids:
        user_records = Attendance.query.filter(
            Attendance.user_id == user_id,
            Attendance.date >= start_date_obj,
            Attendance.date <= end_date_obj
        ).all()

        if user_records:
            user = User.query.get(user_id)
            present = sum(1 for r in user_records if r.status == 'present')
            absent = sum(1 for r in user_records if r.status == 'absent')
            total = present + absent
            percentage = round((present / total * 100), 1) if total > 0 else 0

            attendance_data.append({
                'username': user.username,
                'total_days': total,
                'present': present,
                'absent': absent,
                'percentage': percentage
            })

    return render_template('reports.html',
                         users=users,
                         selected_user_id=selected_user_id,
                         start_date=start_date,
                         end_date=end_date,
                         attendance_data=attendance_data,
                         records=records)


@app.route('/export-csv')
@login_required
def export_csv():
    if current_user.role != 'admin':
        flash('Only admins can export data', 'error')
        return redirect(url_for('dashboard'))

    selected_user_id = request.args.get('user_id', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    start_date_obj = datetime.fromisoformat(start_date) if start_date else datetime.now().date().replace(day=1)
    end_date_obj = datetime.fromisoformat(end_date) if end_date else datetime.now().date()

    filters = [Attendance.date >= start_date_obj, Attendance.date <= end_date_obj]
    if selected_user_id:
        filters.append(Attendance.user_id == int(selected_user_id))

    records = Attendance.query.filter(*filters).order_by(Attendance.date.desc()).all()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date', 'Username', 'Status', 'Marked By', 'Created At'])

    for record in records:
        writer.writerow([
            record.date.strftime('%Y-%m-%d'),
            record.user.username,
            record.status,
            record.marked_by,
            record.created_at.strftime('%Y-%m-%d %H:%M:%S')
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'attendance_report_{start_date}_to_{end_date}.csv'
    )


@app.route('/manage-users')
@login_required
def manage_users():
    if current_user.role != 'admin':
        flash('Only admins can manage users', 'error')
        return redirect(url_for('dashboard'))

    users = User.query.order_by(User.created_at.desc()).all()
    total_users = len(users)
    admin_count = sum(1 for u in users if u.role == 'admin')
    member_count = total_users - admin_count

    return render_template('manage_users.html',
                         users=users,
                         total_users=total_users,
                         admin_count=admin_count,
                         member_count=member_count)


@app.route('/delete-user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if current_user.role != 'admin':
        flash('Only admins can delete users', 'error')
        return redirect(url_for('dashboard'))

    if user_id == current_user.id:
        flash('You cannot delete your own account', 'error')
        return redirect(url_for('manage_users'))

    user = User.query.get(user_id)
    if user:
        # Delete attendance records first
        Attendance.query.filter_by(user_id=user_id).delete()
        db.session.delete(user)
        db.session.commit()
        flash(f'User {user.username} deleted successfully', 'success')

    return redirect(url_for('manage_users'))


# Initialize database
with app.app_context():
    db.create_all()
    # Create default admin if no users exist
    if not User.query.first():
        admin = User(username='admin', role='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print('Default admin user created (username: admin, password: admin123)')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
