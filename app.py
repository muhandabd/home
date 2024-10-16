from datetime import timedelta, datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import barcode
from barcode.writer import ImageWriter
from flask_bcrypt import Bcrypt
import random
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from arabic_reshaper import reshape
from bidi.algorithm import get_display

app = Flask(__name__)
app.secret_key = 'your_secret_key'
bcrypt = Bcrypt(app)

# إعدادات الأمان للجلسات
app.config['SESSION_COOKIE_SECURE'] = False  # يجب تغييره إلى True في الإنتاج
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# دالة لإنشاء اتصال بقاعدة البيانات
def get_db_connection():
    try:
        conn = sqlite3.connect('inventory.db')
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        print(f"خطأ في الاتصال بقاعدة البيانات: {e}")
        return None

# دالة لإنشاء جدول المخزون
def create_inventory_table():
    conn = get_db_connection()
    if conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                quantity REAL NOT NULL,
                expiry_date TEXT NOT NULL,
                barcode_image TEXT,
                barcode_number TEXT,
                category TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()

# دالة لإنشاء جدول الأطباق
def create_dishes_table():
    conn = get_db_connection()
    if conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS dishes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dish_name TEXT NOT NULL,
                ingredients TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()

# تسجيل الخط العربي
def register_fonts():
    arabic_font_path = "static/fonts/Amiri-Regular.ttf"
    pdfmetrics.registerFont(TTFont('Arabic', arabic_font_path))

# توليد باركود وحفظه كصورة
def generate_barcode(item_name):
    conn = get_db_connection()
    barcode_number = ''.join([str(random.randint(0, 9)) for _ in range(12)])
    
    existing_barcode = conn.execute('SELECT * FROM inventory WHERE barcode_number = ?', (barcode_number,)).fetchone()
    while existing_barcode:
        barcode_number = ''.join([str(random.randint(0, 9)) for _ in range(12)])
        existing_barcode = conn.execute('SELECT * FROM inventory WHERE barcode_number = ?', (barcode_number,)).fetchone()
    
    try:
        ean_class = barcode.get_barcode_class('ean13')
        ean = ean_class(barcode_number, writer=ImageWriter())
        filename = f"{item_name}"  # استخدم اسم العنصر كاسم ملف
        full_path = os.path.join('static/barcodes/', filename)  # المسار هنا
        os.makedirs(os.path.dirname(full_path), exist_ok=True)  # إنشاء المجلد إذا لم يكن موجودًا
        ean.save(full_path)  # حفظ الصورة
        return barcode_number, f"{filename}.png"  # إرجاع اسم الملف
    except Exception as e:
        print(f"خطأ في توليد الباركود: {e}")
        raise

# قاعدة بيانات مستخدمين مؤقتة
users_db = {
    "ahmed": bcrypt.generate_password_hash("password123").decode('utf-8')
}

# تسجيل الدخول
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username in users_db and bcrypt.check_password_hash(users_db[username], password):
            session['username'] = username
            return redirect(url_for('manage_inventory'))
        else:
            return render_template('login.html', error="اسم المستخدم أو كلمة المرور غير صحيحة.")
    return render_template('login.html')

# تسجيل الخروج
@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

# الصفحة الرئيسية
@app.route('/')
def home():
    if 'username' in session:
        return f"مرحباً {session['username']}!"
    return redirect(url_for('login'))

# إدارة المخزون
@app.route('/inventory', methods=['GET', 'POST'])
def manage_inventory():
    conn = get_db_connection()
    if 'username' not in session:
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        item_name = request.form['item_name']
        quantity = float(request.form['quantity'])
        expiry_date = request.form['expiry_date']
        category = request.form['category']

        existing_item = conn.execute('SELECT * FROM inventory WHERE name = ?', (item_name,)).fetchone()

        if existing_item:
            conn.execute('UPDATE inventory SET quantity = ?, expiry_date = ?, category = ? WHERE name = ?', 
                         (quantity, expiry_date, category, item_name))
        else:
            barcode_number, barcode_image = generate_barcode(item_name)
            conn.execute('INSERT INTO inventory (name, quantity, expiry_date, barcode_image, barcode_number, category) VALUES (?, ?, ?, ?, ?, ?)', 
                         (item_name, quantity, expiry_date, barcode_image, barcode_number, category))
        conn.commit()

    search_query = request.args.get('search', '')
    category_filter = request.args.get('category', '')
    expiry_days = request.args.get('expiry_days')
    quantity_filter = request.args.get('quantity_filter')

    query = 'SELECT * FROM inventory WHERE 1=1'
    params = []

    if search_query:
        query += ' AND (name LIKE ? OR barcode_number LIKE ?)'
        params.append(f'%{search_query}%')
        params.append(f'%{search_query}%')

    if category_filter and category_filter != 'الكل':
        query += ' AND category = ?'
        params.append(category_filter)

    if expiry_days:
        today = datetime.today().date()
        filter_date = today + timedelta(days=int(expiry_days))
        query += ' AND expiry_date <= ?'
        params.append(filter_date)

    if quantity_filter:
        query += ' AND quantity <= ?'
        params.append(quantity_filter)

    items = conn.execute(query, params).fetchall()

    today = datetime.today().date()
    alerts = []
    for item in items:
        expiry_date = datetime.strptime(item['expiry_date'], '%Y-%m-%d').date()
        days_left = (expiry_date - today).days
        if days_left <= 5:
            alerts.append({
                'name': item['name'],
                'days_left': days_left,
                'expiry_date': item['expiry_date']
            })

    conn.close()
    return render_template('inventory.html', inventory=items, alerts=alerts, search_query=search_query, category_filter=category_filter)

# استيراد البيانات من Excel
@app.route('/import_excel', methods=['GET', 'POST'])
def import_excel():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('لم يتم تحديد أي ملف!')
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            flash('لم يتم اختيار أي ملف!')
            return redirect(request.url)

        if file and file.filename.endswith('.xlsx'):
            try:
                df = pd.read_excel(file)
                df.columns = df.columns.str.strip().str.lower()
                required_columns = {'name', 'quantity', 'expiry date', 'barcode number', 'category'}

                if not required_columns.issubset(set(df.columns)):
                    missing_columns = required_columns - set(df.columns)
                    flash(f'الملف لا يحتوي على الأعمدة المطلوبة! الأعمدة المفقودة: {", ".join(missing_columns)}')
                    return redirect(request.url)

                conn = get_db_connection()
                conn.execute('DELETE FROM inventory')
                conn.commit()

                for index, row in df.iterrows():
                    conn.execute('INSERT INTO inventory (name, quantity, expiry_date, barcode_number, category) VALUES (?, ?, ?, ?, ?)', 
                                 (row['name'], row['quantity'], row['expiry date'], row['barcode number'], row['category']))
                conn.commit()
                conn.close()

                flash('تم استيراد البيانات بنجاح!')
                return redirect(url_for('manage_inventory'))

            except Exception as e:
                flash(f'حدث خطأ أثناء قراءة الملف: {str(e)}')
                return redirect(request.url)
        else:
            flash('يجب أن يكون الملف من نوع Excel (.xlsx)')
            return redirect(request.url)
    
    return render_template('import_excel.html')

# اقتراح الأطباق
@app.route('/suggest_dishes')
def suggest_dishes():
    conn = get_db_connection()
    dishes = conn.execute('SELECT * FROM dishes').fetchall()
    conn.close()

    suggestions = []
    for dish in dishes:
        available, missing = check_ingredients_availability(dish['dish_name'])
        if available:
            suggestions.append({'dish_name': dish['dish_name'], 'status': 'متوفر'})
        else:
            suggestions.append({'dish_name': dish['dish_name'], 'status': f"غير متوفر، المكونات الناقصة: {', '.join(missing)}"})

    return render_template('suggest_dishes.html', suggestions=suggestions)

@app.route('/edit_dish/<dish_name>', methods=['GET', 'POST'])
def edit_dish(dish_name):
    conn = get_db_connection()
    dish = conn.execute('SELECT * FROM dishes WHERE dish_name = ?', (dish_name,)).fetchone()
    conn.close()

    if not dish:
        return "الطبق غير موجود!", 404

    if request.method == 'POST':
        new_ingredients = request.form['ingredients']
        conn = get_db_connection()
        conn.execute('UPDATE dishes SET ingredients = ? WHERE dish_name = ?', (new_ingredients, dish_name))
        conn.commit()
        conn.close()
        flash(f"تم تعديل الطبق {dish_name} بنجاح!")
        return redirect(url_for('edit_dish', dish_name=dish_name))

    return render_template('edit_dish.html', dish=dish)

# دالة للتحقق من توافر المكونات
def check_ingredients_availability(dish_name):
    conn = get_db_connection()
    dish = conn.execute('SELECT * FROM dishes WHERE dish_name = ?', (dish_name,)).fetchone()

    if dish:
        ingredients = dish['ingredients'].split(', ')
        missing_ingredients = []

        for ingredient in ingredients:
            item = conn.execute('SELECT * FROM inventory WHERE name LIKE ?', (f"%{ingredient}%",)).fetchone()
            if not item:
                missing_ingredients.append(ingredient)

        conn.close()
        if missing_ingredients:
            return False, missing_ingredients
        return True, []

    conn.close()
    return False, ["الطبق غير موجود"]

# إضافة طبق جديد
@app.route('/add_dish', methods=['GET', 'POST'])
def add_dish():
    if request.method == 'POST':
        dish_name = request.form['dish_name']
        ingredients = request.form['ingredients']

        conn = get_db_connection()
        conn.execute('INSERT INTO dishes (dish_name, ingredients) VALUES (?, ?)', (dish_name, ingredients))
        conn.commit()
        conn.close()

        flash(f"تم إضافة الطبق {dish_name} بنجاح!")
        return redirect(url_for('add_dish'))

    return render_template('add_dish.html')

# تصدير البيانات إلى Excel
@app.route('/export_excel')
def export_excel():
    conn = get_db_connection()
    items = conn.execute('SELECT name, quantity, expiry_date, barcode_image, barcode_number, category FROM inventory').fetchall()
    conn.close()

    # إعداد البيانات للتصدير
    data = [{
        'Name': item['name'],
        'Quantity': item['quantity'],
        'Expiry Date': item['expiry_date'],
        'Barcode Number': item['barcode_number'],
        'Category': item['category']
    } for item in items]

    # تحويل البيانات إلى DataFrame باستخدام pandas
    df = pd.DataFrame(data)

    # تحديد مسار الحفظ لملف Excel
    file_path = 'static/inventory_export.xlsx'

    # حفظ البيانات في ملف Excel
    df.to_excel(file_path, index=False)

    return redirect(url_for('static', filename='inventory_export.xlsx'))

# صفحة تصدير PDF
@app.route('/export_pdf')
def export_pdf():
    conn = get_db_connection()
    items = conn.execute('SELECT * FROM inventory').fetchall()
    conn.close()

    pdf_file_path = 'static/inventory_report.pdf'
    c = canvas.Canvas(pdf_file_path, pagesize=A4)
    width, height = A4
    register_fonts()

    # إعداد عنوان التقرير
    c.setFont("Arabic", 16)
    c.drawRightString(500, height - 50, get_display(reshape("تقرير المخزون")))

    y_position = height - 100
    c.setFont("Arabic", 12)
    
    # إعداد عناوين الأعمدة
    c.drawRightString(450, y_position, get_display(reshape("اسم المنتج")))
    c.drawRightString(300, y_position, get_display(reshape("الكمية")))
    c.drawRightString(150, y_position, get_display(reshape("تاريخ الصلاحية")))

    y_position -= 20
    c.setFont("Arabic", 10)
    
    # إضافة بيانات المخزون
    for item in items:
        c.drawRightString(450, y_position, get_display(reshape(item['name'])))
        c.drawRightString(300, y_position, str(item['quantity']))
        c.drawRightString(150, y_position, item['expiry_date'])
        y_position -= 20

        # الانتقال إلى صفحة جديدة إذا كانت المسافة أقل من 50
        if y_position < 50:
            c.showPage()
            y_position = height - 100

    # حفظ ملف PDF
    c.save()

    return redirect(url_for('static', filename='inventory_report.pdf'))

# تعديل العنصر
@app.route('/edit_item/<item_name>', methods=['GET', 'POST'])
def edit_item(item_name):
    conn = get_db_connection()
    item = conn.execute('SELECT * FROM inventory WHERE name = ?', (item_name,)).fetchone()
    if not item:
        conn.close()
        return "العنصر غير موجود!", 404
    if request.method == 'POST':
        quantity = request.form['quantity']
        expiry_date = request.form['expiry_date']
        category = request.form['category']
        conn.execute('UPDATE inventory SET quantity = ?, expiry_date = ?, category = ? WHERE name = ?', (quantity, expiry_date, category, item_name))
        conn.commit()
        conn.close()
        return redirect(url_for('manage_inventory'))
    conn.close()
    return render_template('edit_item.html', item=item)

# حذف العنصر
@app.route('/delete_item/<item_name>', methods=['POST'])
def delete_item(item_name):
    conn = get_db_connection()
    conn.execute('DELETE FROM inventory WHERE name = ?', (item_name,))
    conn.commit()
    conn.close()
    return redirect(url_for('manage_inventory'))

@app.route('/reports')
def reports():
    conn = get_db_connection()
    inventory_report = conn.execute('SELECT * FROM inventory').fetchall()
    conn.close()
    return render_template('reports.html', inventory=inventory_report)

# التأكد من إنشاء الجداول عند بدء التطبيق
if __name__ == '__main__':
    create_inventory_table()
    create_dishes_table()
    if not os.path.exists('static/barcodes/'):
        os.makedirs('static/barcodes')
    app.run(debug=True, host='0.0.0.0')