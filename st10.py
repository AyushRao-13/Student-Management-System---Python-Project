"""
Student Management System (Normalized SQLite)
- Normalized DB schema:
    students(uid PK), subjects(id PK, name UNIQUE), student_subjects(id PK, student_uid FK, subject_id FK, mark)
- Pragma settings tuned for stronger durability (WAL, synchronous=FULL)
- All multi-step operations are done in transactions (with self.conn:)
- UI, PDF/CSV, images preserved from original app
"""

import os
import shutil
import tempfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sqlite3

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
    TB_AVAILABLE = True
except Exception:
    TB_AVAILABLE = False

from PIL import Image, ImageTk
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as PDFImage, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib import colors

# -------------------------
# Model
# -------------------------
class Student:
    def __init__(self, uid, name, student_class, section, subjects=None, marks=None, image_filename=""):
        self.uid = uid
        self.name = name
        self.student_class = student_class
        self.section = section
        self.subjects = subjects or []
        self.marks = marks or []
        self.image_filename = image_filename

    def to_dict(self):
        return {
            "uid": self.uid,
            "name": self.name,
            "student_class": self.student_class,
            "section": self.section,
            "subjects": self.subjects,
            "marks": self.marks,
            "image_filename": self.image_filename
        }

# -------------------------
# Application
# -------------------------
class StudentManagementApp:
    DB_FILE = os.path.join(os.path.dirname(__file__), "students.db")
    IMAGES_DIR = "images"
    ICONS_DIR = "icons"
    DEFAULT_IMG = os.path.join(IMAGES_DIR, "default.jpg")

    def __init__(self, root):
        # root may be tb.Window or tk.Tk depending on availability
        self.root = root
        self.root.title("🎓 Student Management System")
        try:
            self.root.state("zoomed")
        except Exception:
            self.root.geometry("1100x700")

        os.makedirs(self.IMAGES_DIR, exist_ok=True)

        # load icons (optional)
        self.icons = {}
        self._load_icons()

        # init DB (normalized)
        self._init_db()

        # in-memory
        self.students = []
        self.load_data()

        # style
        if TB_AVAILABLE:
            style = tb.Style(theme="cosmo")
        else:
            style = ttk.Style()
            try:
                style.theme_use("clam")
            except Exception:
                pass

        # --- Top header ---
        header = ttk.Frame(self.root, padding=(12, 8))
        header.pack(fill=tk.X)

        title_lbl = ttk.Label(header, text="Student Management System", font=("Segoe UI", 18, "bold"))
        title_lbl.pack(side=tk.LEFT)

        right_actions = ttk.Frame(header)
        right_actions.pack(side=tk.RIGHT)

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(right_actions, textvariable=self.search_var, width=28)
        search_entry.pack(side=tk.LEFT, padx=(0,6))
        search_entry.bind("<Return>", lambda e: self.search_students())

        btn_search = ttk.Button(right_actions, text="Search", command=self.search_students, image=self.icons.get("search"), compound=tk.LEFT)
        btn_search.pack(side=tk.LEFT, padx=4)

        btn_reset = ttk.Button(right_actions, text="Reset", command=self.reset_filters_and_refresh)
        btn_reset.pack(side=tk.LEFT, padx=4)

        # --- Toolbar ---
        toolbar = ttk.Frame(self.root, padding=(12,6))
        toolbar.pack(fill=tk.X)

        btn_add = ttk.Button(toolbar, text=" Add", image=self.icons.get("add"), compound=tk.LEFT, command=self.add_student_form)
        btn_add.pack(side=tk.LEFT, padx=6)

        btn_update = ttk.Button(toolbar, text=" Update", image=self.icons.get("update"), compound=tk.LEFT, command=self.update_student_form)
        btn_update.pack(side=tk.LEFT, padx=6)

        btn_delete = ttk.Button(toolbar, text=" Delete", image=self.icons.get("delete"), compound=tk.LEFT, command=self.delete_student)
        btn_delete.pack(side=tk.LEFT, padx=6)

        btn_csv = ttk.Button(toolbar, text=" Export CSV", image=self.icons.get("csv"), compound=tk.LEFT, command=self.export_csv)
        btn_csv.pack(side=tk.LEFT, padx=6)

        btn_exit = ttk.Button(toolbar, text=" Exit", image=self.icons.get("exit"), compound=tk.LEFT, command=self._on_exit)
        btn_exit.pack(side=tk.RIGHT, padx=6)

        # --- Filter & sort panel ---
        filter_frame = ttk.Labelframe(self.root, text="Filters & Sorting", padding=8)
        filter_frame.pack(fill=tk.X, padx=12, pady=(6,8))

        ttk.Label(filter_frame, text="Class:").pack(side=tk.LEFT, padx=(6,4))
        self.filter_class = tk.StringVar(value="All")
        self.class_combo = ttk.Combobox(filter_frame, textvariable=self.filter_class, width=12, state="readonly")
        self.class_combo.pack(side=tk.LEFT, padx=(0,12))

        ttk.Label(filter_frame, text="Grade:").pack(side=tk.LEFT, padx=(0,4))
        self.filter_grade = tk.StringVar(value="All")
        self.grade_combo = ttk.Combobox(filter_frame, textvariable=self.filter_grade, width=8, state="readonly", values=["All","A","B","C"])
        self.grade_combo.pack(side=tk.LEFT, padx=(0,12))

        ttk.Label(filter_frame, text="Min Avg:").pack(side=tk.LEFT, padx=(0,4))
        self.min_avg_var = tk.StringVar()
        ttk.Entry(filter_frame, width=6, textvariable=self.min_avg_var).pack(side=tk.LEFT, padx=(0,8))
        ttk.Label(filter_frame, text="Max Avg:").pack(side=tk.LEFT, padx=(0,4))
        self.max_avg_var = tk.StringVar()
        ttk.Entry(filter_frame, width=6, textvariable=self.max_avg_var).pack(side=tk.LEFT, padx=(0,12))

        ttk.Label(filter_frame, text="Sort by:").pack(side=tk.LEFT, padx=(0,4))
        self.sort_by = tk.StringVar(value="UID")
        self.sort_combo = ttk.Combobox(filter_frame, textvariable=self.sort_by, width=12, state="readonly", values=["UID","Name","Class","Average"])
        self.sort_combo.pack(side=tk.LEFT, padx=(0,6))

        self.sort_asc = tk.BooleanVar(value=True)
        ttk.Checkbutton(filter_frame, text="Asc", variable=self.sort_asc).pack(side=tk.LEFT, padx=(0,12))

        ttk.Button(filter_frame, text="Apply", command=self.apply_filters).pack(side=tk.LEFT, padx=6)
        ttk.Button(filter_frame, text="Clear", command=self.reset_filters_and_refresh).pack(side=tk.LEFT, padx=6)

        # --- Table area ---
        table_frame = ttk.Frame(self.root)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0,12))

        columns = ("UID", "Name", "Class", "Section", "Average", "Grade")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for col in columns:
            self.tree.heading(col, text=col, command=lambda _col=col: self.sort_by_column(_col))
            self.tree.column(col, anchor="center", width=110)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<Double-1>", self.show_student_profile)

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=vsb.set)

        self.tree.tag_configure('oddrow', background='#FAFAFA')
        self.tree.tag_configure('evenrow', background='#EFEFEF')

        self._update_filter_options()
        self.refresh_table()

        self.root.bind("<Control-n>", lambda e: self.add_student_form())
        self.root.bind("<Control-s>", lambda e: self.export_csv())

        self.root.protocol("WM_DELETE_WINDOW", self._on_exit)

    # -------------------------
    # Icons loader
    # -------------------------
    def _load_icons(self):
        icon_map = {
            "add": "add.png",
            "update": "update.png",
            "delete": "delete.png",
            "search": "search.png",
            "csv": "csv.png",
            "pdf": "pdf.png",
            "exit": "exit.png"
        }
        for name, fname in icon_map.items():
            path = os.path.join(self.ICONS_DIR, fname)
            if os.path.exists(path):
                try:
                    im = Image.open(path)
                    im = im.resize((18, 18), Image.LANCZOS)
                    self.icons[name] = ImageTk.PhotoImage(im)
                except Exception:
                    self.icons[name] = None
            else:
                self.icons[name] = None

    # -------------------------
    # Database (normalized)
    # -------------------------
    def _init_db(self):
        """Initialize DB, pragmas, and normalized tables."""
        try:
            self.conn = sqlite3.connect(self.DB_FILE, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.cur = self.conn.cursor()
            # stronger ACID-ish pragmas for SQLite; WAL improves concurrency, synchronous=FULL prioritizes durability
            try:
                self.cur.execute("PRAGMA foreign_keys = ON")
                self.cur.execute("PRAGMA journal_mode = WAL")
                self.cur.execute("PRAGMA synchronous = FULL")
            except Exception:
                pass

            # Tables: students, subjects (unique names), student_subjects (mapping + mark)
            self.cur.execute("""
                CREATE TABLE IF NOT EXISTS students (
                    uid TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    student_class TEXT,
                    section TEXT,
                    image_filename TEXT
                )
            """)
            self.cur.execute("""
                CREATE TABLE IF NOT EXISTS subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                )
            """)
            self.cur.execute("""
                CREATE TABLE IF NOT EXISTS student_subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_uid TEXT NOT NULL,
                    subject_id INTEGER NOT NULL,
                    mark REAL,
                    FOREIGN KEY(student_uid) REFERENCES students(uid) ON DELETE CASCADE,
                    FOREIGN KEY(subject_id) REFERENCES subjects(id) ON DELETE CASCADE
                )
            """)
            self.conn.commit()
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to initialize database: {e}")

    def _get_subject_id(self, subject_name):
        """Return subject id for a subject name, inserting if needed."""
        if not subject_name:
            return None
        name = subject_name.strip()
        if not name:
            return None
        try:
            with self.conn:
                self.cur.execute("INSERT OR IGNORE INTO subjects (name) VALUES (?)", (name,))
                # now fetch id
                self.cur.execute("SELECT id FROM subjects WHERE name = ?", (name,))
                row = self.cur.fetchone()
                return row["id"] if row else None
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to get subject id for '{subject_name}': {e}")
            return None

    def db_upsert_student_normalized(self, student: Student):
        """
        Insert or update a student and its subjects/marks transactionally.
        Strategy:
          - Insert or replace into students (metadata)
          - Delete existing student_subjects rows for that student
          - For each subject, ensure subject row exists and insert mapping row with mark
        """
        try:
            with self.conn:  # transaction: commit on success, rollback on exception
                # upsert student metadata
                self.cur.execute("""
                    INSERT OR REPLACE INTO students (uid, name, student_class, section, image_filename)
                    VALUES (?, ?, ?, ?, ?)
                """, (student.uid, student.name, student.student_class, student.section, student.image_filename or ""))
                # delete previous subject mappings for the student
                self.cur.execute("DELETE FROM student_subjects WHERE student_uid = ?", (student.uid,))
                # insert new mappings
                for subj, mk in zip(student.subjects, student.marks):
                    sid = self._get_subject_id(subj)
                    if sid is None:
                        continue
                    # coerce mark to float if possible
                    try:
                        mark_val = float(mk)
                    except Exception:
                        mark_val = None
                    self.cur.execute("""
                        INSERT INTO student_subjects (student_uid, subject_id, mark)
                        VALUES (?, ?, ?)
                    """, (student.uid, sid, mark_val))
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to save student {student.uid}: {e}")

    def db_delete_student_normalized(self, uid):
        """Delete student (student_subjects cascade due to FK)."""
        try:
            with self.conn:
                self.cur.execute("DELETE FROM students WHERE uid = ?", (uid,))
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to delete student {uid}: {e}")

    def load_data(self):
        """Load students and their subjects/marks from normalized DB into memory."""
        self.students = []
        try:
            # fetch all students
            self.cur.execute("SELECT uid, name, student_class, section, image_filename FROM students")
            students_rows = self.cur.fetchall()
            for srow in students_rows:
                uid = srow["uid"]
                name = srow["name"]
                sclass = srow["student_class"]
                section = srow["section"]
                image = srow["image_filename"]
                # fetch subjects/marks for this student
                self.cur.execute("""
                    SELECT sub.name AS subject, ss.mark AS mark
                    FROM student_subjects ss
                    JOIN subjects sub ON ss.subject_id = sub.id
                    WHERE ss.student_uid = ?
                    ORDER BY sub.name
                """, (uid,))
                sm_rows = self.cur.fetchall()
                subjects = []
                marks = []
                for r in sm_rows:
                    subjects.append(r["subject"])
                    # store marks as strings (consistent with previous app behavior)
                    marks.append(str(r["mark"]) if r["mark"] is not None else "")
                self.students.append(Student(uid, name, sclass, section, subjects, marks, image))
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to load students from database: {e}")

    def save_data(self):
        """
        Reconcile in-memory students into DB.
        Transactionally:
          - Upsert every in-memory student (and their subjects/marks)
          - Delete DB students not present in memory
        """
        try:
            in_memory_uids = [s.uid for s in self.students]
            # upsert each student (and their subject mappings) in their own transaction
            for s in self.students:
                self.db_upsert_student_normalized(s)
            # delete DB students not present in memory (single transaction)
            with self.conn:
                if in_memory_uids:
                    placeholders = ",".join(["?"] * len(in_memory_uids))
                    sql = f"DELETE FROM students WHERE uid NOT IN ({placeholders})"
                    self.cur.execute(sql, in_memory_uids)
                else:
                    # no students in memory -> clear students table
                    self.cur.execute("DELETE FROM students")
        except Exception as e:
            messagebox.showerror("Database Error", f"Failed to sync database: {e}")

    # -------------------------
    # Helpers: averages & grade
    # -------------------------
    def _avg_of(self, student):
        try:
            if not student.marks:
                return None
            vals = [float(x) for x in student.marks if x != ""]
            if not vals:
                return None
            return round(sum(vals)/len(vals), 2)
        except Exception:
            return None

    def _grade_of(self, avg):
        if avg is None:
            return "N/A"
        if avg >= 90:
            return "A"
        if avg >= 75:
            return "B"
        return "C"

    # -------------------------
    # Table operations
    # -------------------------
    def refresh_table(self, student_list=None):
        data = student_list if student_list is not None else list(self.students)
        data = self._apply_sorting(data)
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, s in enumerate(data):
            avg = self._avg_of(s)
            avg_display = str(avg) if avg is not None else "-"
            grade = self._grade_of(avg)
            tag = 'evenrow' if i % 2 == 0 else 'oddrow'
            self.tree.insert("", tk.END, values=(s.uid, s.name, s.student_class, s.section, avg_display, grade), tags=(tag,))
        self._update_filter_options()

    def search_students(self):
        """Enhanced search: matches against name, UID, class, section, subjects, and marks."""
        q = self.search_var.get().strip().lower()
        if not q:
            self.refresh_table()
            return

        results = []
        for s in self.students:
            # Combine all searchable fields into one lowercase string
            combined = " ".join([
                str(s.uid or ""),
                str(s.name or ""),
                str(s.student_class or ""),
                str(s.section or ""),
                " ".join(s.subjects or []),
                " ".join(s.marks or [])
            ]).lower()

        if q in combined:
            results.append(s)

        self.refresh_table(results)


    # -------------------------
    # Filtering & Sorting
    # -------------------------
    def apply_filters(self):
        selected_class = self.filter_class.get()
        selected_grade = self.filter_grade.get()
        min_avg = None
        max_avg = None
        try:
            if self.min_avg_var.get().strip() != "":
                min_avg = float(self.min_avg_var.get().strip())
            if self.max_avg_var.get().strip() != "":
                max_avg = float(self.max_avg_var.get().strip())
        except ValueError:
            messagebox.showerror("Error", "Min/Max averages must be numeric.")
            return

        filtered = []
        for s in self.students:
            avg = self._avg_of(s)
            grade = self._grade_of(avg)

            if selected_class and selected_class != "All" and s.student_class != selected_class:
                continue
            if selected_grade and selected_grade != "All" and grade != selected_grade:
                continue
            if min_avg is not None and (avg is None or avg < min_avg):
                continue
            if max_avg is not None and (avg is None or avg > max_avg):
                continue
            filtered.append(s)
        self.refresh_table(filtered)

    def reset_filters_and_refresh(self):
        self.search_var.set("")
        self.filter_class.set("All")
        self.filter_grade.set("All")
        self.min_avg_var.set("")
        self.max_avg_var.set("")
        self.sort_by.set("UID")
        self.sort_asc.set(True)
        self._update_filter_options()
        self.refresh_table()

    def _update_filter_options(self):
        classes = sorted({s.student_class for s in self.students if s.student_class})
        vals = ["All"] + classes
        self.class_combo['values'] = vals
        if self.filter_class.get() not in vals:
            self.filter_class.set("All")

    def _apply_sorting(self, data_list):
        key = self.sort_by.get()
        reverse = not self.sort_asc.get()
        try:
            if key == "UID":
                return sorted(data_list, key=lambda s: self._uid_sort_key(s.uid), reverse=reverse)
            if key == "Name":
                return sorted(data_list, key=lambda s: (s.name or "").lower(), reverse=reverse)
            if key == "Class":
                return sorted(data_list, key=lambda s: (s.student_class or "").lower(), reverse=reverse)
            if key == "Average":
                def avg_key(s):
                    a = self._avg_of(s)
                    is_none = a is None
                    val = a if a is not None else 0
                    return (is_none, val)
                return sorted(data_list, key=avg_key, reverse=reverse)
        except Exception:
            return data_list
        return data_list

    def _uid_sort_key(self, uid):
        try:
            return int(uid)
        except Exception:
            return (uid or "").lower()

    def sort_by_column(self, col):
        if col in ("UID", "Name", "Class", "Average"):
            current = self.sort_by.get()
            if current == col:
                self.sort_asc.set(not self.sort_asc.get())
            else:
                self.sort_by.set(col)
                self.sort_asc.set(True)
            self.refresh_table()

    # -------------------------
    # Add / Update form
    # -------------------------
    def add_student_form(self):
        self._student_form(mode="add")

    def update_student_form(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a student to update.")
            return
        uid = self.tree.item(selected[0], "values")[0]
        student = next((s for s in self.students if s.uid == uid), None)
        if student:
            self._student_form(mode="update", student=student)

    def _student_form(self, mode="add", student=None):
        form = tk.Toplevel(self.root)
        form.title("Add Student" if mode == "add" else "Update Student")
        form.transient(self.root)
        w = max(560, int(self.root.winfo_width() * 0.6))
        h = max(420, int(self.root.winfo_height() * 0.6))
        form.geometry(f"{w}x{h}")
        form.minsize(520, 420)
        form.grab_set()

        labels = ["UID", "Name", "Class", "Section", "Subjects (comma separated)", "Marks (comma separated)"]
        entries = {}

        frm = ttk.Frame(form, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        for i, label in enumerate(labels):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=6, padx=(0,6))
            entry = ttk.Entry(frm, width=50)
            entry.grid(row=i, column=1, sticky="ew", pady=6)
            entries[label] = entry

        frm.columnconfigure(1, weight=1)

        # Image controls
        img_path_var = tk.StringVar()
        img_row = len(labels)
        ttk.Label(frm, text="Photo (optional):").grid(row=img_row, column=0, sticky="w", pady=6)
        img_label = ttk.Label(frm, text="No file chosen", width=40)
        img_label.grid(row=img_row, column=1, sticky="w", pady=6)

        def choose_image():
            file = filedialog.askopenfilename(title="Choose student photo",
                                              filetypes=[("Image files","*.png;*.jpg;*.jpeg;*.bmp;*.gif")])
            if file:
                img_path_var.set(file)
                img_label.config(text=os.path.basename(file))
        ttk.Button(frm, text="Browse", command=choose_image).grid(row=img_row, column=2, padx=(6,0))

        if student:
            entries["UID"].insert(0, student.uid)
            entries["UID"].config(state="disabled")
            entries["Name"].insert(0, student.name)
            entries["Class"].insert(0, student.student_class)
            entries["Section"].insert(0, student.section)
            entries["Subjects (comma separated)"].insert(0, ", ".join(student.subjects))
            entries["Marks (comma separated)"].insert(0, ", ".join(student.marks))
            if student.image_filename:
                img_label.config(text=os.path.basename(student.image_filename))
                img_path_var.set(student.image_filename)

        def save():
            uid = entries["UID"].get().strip()
            name = entries["Name"].get().strip()
            student_class = entries["Class"].get().strip()
            section = entries["Section"].get().strip()
            subjects = [s.strip() for s in entries["Subjects (comma separated)"].get().split(",") if s.strip()]
            marks_input = [m.strip() for m in entries["Marks (comma separated)"].get().split(",") if m.strip()]

            if not (uid and name and student_class):
                messagebox.showerror("Error", "UID, Name and Class are required.")
                return

            if subjects and len(subjects) != len(marks_input):
                messagebox.showerror("Error", "Number of subjects and marks must match.")
                return

            marks = []
            for m in marks_input:
                try:
                    marks.append(str(float(m)))
                except ValueError:
                    messagebox.showerror("Error", "Marks must be numeric.")
                    return

            image_target_rel = ""
            chosen = img_path_var.get()
            if chosen:
                try:
                    abs_images = os.path.abspath(self.IMAGES_DIR)
                    abs_chosen = os.path.abspath(chosen)
                    try:
                        common = os.path.commonpath([abs_images, abs_chosen])
                    except Exception:
                        common = ""
                    if common == abs_images:
                        image_target_rel = os.path.relpath(abs_chosen)
                    else:
                        ext = os.path.splitext(chosen)[1] or ".jpg"
                        tgt = os.path.join(self.IMAGES_DIR, f"{uid}{ext}")
                        shutil.copyfile(chosen, tgt)
                        image_target_rel = os.path.relpath(tgt)
                except Exception as e:
                    messagebox.showwarning("Warning", f"Failed to copy image: {e}")

            if mode == "add":
                if any(s.uid == uid for s in self.students):
                    messagebox.showerror("Error", "UID already exists.")
                    return
                new = Student(uid, name, student_class, section, subjects, marks, image_target_rel)
                self.students.append(new)
                # DB upsert (normalized)
                self.db_upsert_student_normalized(new)
                messagebox.showinfo("Success", "Student added.")
            else:
                updated = False
                for s in self.students:
                    if s.uid == uid:
                        s.name = name
                        s.student_class = student_class
                        s.section = section
                        s.subjects = subjects
                        s.marks = marks
                        if image_target_rel:
                            s.image_filename = image_target_rel
                        # DB upsert
                        self.db_upsert_student_normalized(s)
                        updated = True
                        messagebox.showinfo("Success", "Student updated.")
                        break
                if not updated:
                    messagebox.showerror("Error", "Student not found in memory.")

            self.load_data()
            self._update_filter_options()
            self.refresh_table()
            form.destroy()

        btn_row = img_row + 1
        ttk.Button(frm, text="Save", command=save).grid(row=btn_row, column=0, columnspan=3, pady=12)

    # -------------------------
    # Delete
    # -------------------------
    def delete_student(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Warning", "Select a student to delete.")
            return
        uid = self.tree.item(selected[0], "values")[0]
        confirm = messagebox.askyesno("Confirm", f"Delete student with UID {uid}?")
        if not confirm:
            return
        # remove from memory and DB (cascades to student_subjects)
        self.students = [s for s in self.students if s.uid != uid]
        self.db_delete_student_normalized(uid)
        self.load_data()
        self._update_filter_options()
        self.refresh_table()
        messagebox.showinfo("Deleted", "Student removed.")

    # -------------------------
    # Profile & PDF
    # -------------------------
    def show_student_profile(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        uid = self.tree.item(selected[0], "values")[0]
        student = next((s for s in self.students if s.uid == uid), None)
        if not student:
            return

        profile = tk.Toplevel(self.root)
        profile.title(f"Profile - {student.name}")
        pw = max(700, int(self.root.winfo_width() * 0.75))
        ph = max(480, int(self.root.winfo_height() * 0.75))
        profile.geometry(f"{pw}x{ph}")
        profile.minsize(700, 480)
        profile.grab_set()

        header = tk.Label(profile, text="Student Profile", font=("Arial", 18, "bold"), bg="#2b5797", fg="white", pady=10)
        header.pack(fill=tk.X)

        content = ttk.Frame(profile, padding=12)
        content.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(content)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=8)

        right = ttk.Frame(content)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)

        photo_frame = ttk.LabelFrame(left, text="Photo", padding=8)
        photo_frame.pack(pady=8)
        img_path = student.image_filename if student.image_filename else os.path.join(self.IMAGES_DIR, "default.jpg")
        if not os.path.exists(img_path):
            img_path = os.path.join(self.IMAGES_DIR, "default.jpg")
        photo = None
        try:
            im = Image.open(img_path)
            im = im.resize((160, 160))
            photo = ImageTk.PhotoImage(im)
        except Exception:
            photo = None
        if photo:
            lbl = ttk.Label(photo_frame, image=photo)
            lbl.image = photo
            lbl.pack()
        else:
            ttk.Label(photo_frame, text="[No Image]").pack()

        details_frame = ttk.LabelFrame(left, text="Details", padding=8)
        details_frame.pack(fill=tk.X, pady=8)

        def add_row(lbl_text, val_text):
            row = ttk.Frame(details_frame)
            row.pack(anchor="w", pady=2)
            ttk.Label(row, text=f"{lbl_text}:", font=("Arial", 10, "bold"), width=12).pack(side=tk.LEFT)
            ttk.Label(row, text=val_text, font=("Arial", 10)).pack(side=tk.LEFT)

        avg = self._avg_of(student)
        grade = self._grade_of(avg)
        add_row("UID", student.uid)
        add_row("Name", student.name)
        add_row("Class", student.student_class)
        add_row("Section", student.section)
        add_row("Average", str(avg) if avg is not None else "N/A")
        add_row("Grade", grade)

        subjects_frame = ttk.LabelFrame(right, text="Subjects & Marks", padding=8)
        subjects_frame.pack(fill=tk.X, pady=6)

        if student.subjects:
            for sub, m in zip(student.subjects, student.marks):
                r = ttk.Frame(subjects_frame)
                r.pack(anchor="w", pady=2)
                ttk.Label(r, text=f"{sub}", width=20, font=("Arial", 10, "bold")).pack(side=tk.LEFT)
                ttk.Label(r, text=f"{m}", font=("Arial", 10)).pack(side=tk.LEFT)
        else:
            ttk.Label(subjects_frame, text="No subjects recorded.", font=("Arial", 10, "italic")).pack(pady=6)

        chart_frame = ttk.LabelFrame(right, text="Performance Chart", padding=8)
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=6)

        fig = None
        if student.subjects and student.marks:
            try:
                fig = Figure(figsize=(5, 3), dpi=100)
                ax = fig.add_subplot(111)
                marks_float = [float(x) for x in student.marks if x != ""]
                ax.bar(student.subjects, marks_float)
                ax.set_ylim(0, 100)
                ax.set_ylabel("Marks")
                for i, v in enumerate(marks_float):
                    ax.text(i, v + 1.5, str(v), ha="center", fontsize=8)
                canvas = FigureCanvasTkAgg(fig, chart_frame)
                canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
                canvas.draw()
            except Exception:
                ttk.Label(chart_frame, text="No marks to plot.", font=("Arial", 10, "italic")).pack(pady=20)
        else:
            ttk.Label(chart_frame, text="No marks to plot.", font=("Arial", 10, "italic")).pack(pady=20)

        btn_frame = ttk.Frame(profile)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Update", width=16, command=lambda: [profile.destroy(), self._student_form(mode="update", student=student)]).grid(row=0, column=0, padx=6)
        ttk.Button(btn_frame, text="Delete", width=16, command=lambda: [profile.destroy(), self._confirm_delete_and_refresh(student.uid)]).grid(row=0, column=1, padx=6)
        ttk.Button(btn_frame, text="Download Report (PDF)", width=20, command=lambda: self.generate_pdf(student, fig if fig is not None else None)).grid(row=0, column=2, padx=6)
        ttk.Button(btn_frame, text="Close", width=12, command=profile.destroy).grid(row=0, column=3, padx=6)

    def _is_number(self, s):
        try:
            float(s)
            return True
        except Exception:
            return False

    def _confirm_delete_and_refresh(self, uid):
        confirm = messagebox.askyesno("Confirm", f"Delete student {uid}?")
        if confirm:
            self.students = [s for s in self.students if s.uid != uid]
            self.db_delete_student_normalized(uid)
            self._update_filter_options()
            self.refresh_table()
            messagebox.showinfo("Deleted", "Student removed.")

    # -------------------------
    # PDF generation
    # -------------------------
    def generate_pdf(self, student, fig=None):
        savep = filedialog.asksaveasfilename(title="Save PDF", defaultextension=".pdf", filetypes=[("PDF files","*.pdf")], initialfile=f"{student.uid}_{student.name}_Report.pdf")
        if not savep:
            return
        try:
            doc = SimpleDocTemplate(savep, pagesize=A4)
            styles = getSampleStyleSheet()
            story = []

            story.append(Paragraph(f"<b>Student Report Card</b>", styles['Title']))
            story.append(Spacer(1, 12))

            if student.image_filename and os.path.exists(student.image_filename):
                story.append(PDFImage(student.image_filename, width=1.6*inch, height=1.6*inch))
                story.append(Spacer(1, 8))

            info_data = [
                ["UID", student.uid],
                ["Name", student.name],
                ["Class", student.student_class],
                ["Section", student.section],
            ]
            avg = self._avg_of(student)
            grade = self._grade_of(avg)
            info_data.append(["Average", str(avg) if avg is not None else "N/A"])
            info_data.append(["Grade", grade])
            t = Table(info_data, colWidths=[1.2*inch, 3.8*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.whitesmoke),
                ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
            ]))
            story.append(t)
            story.append(Spacer(1, 12))

            if student.subjects:
                marks_table = [["Subject", "Marks"]]
                for sub, m in zip(student.subjects, student.marks):
                    marks_table.append([sub, str(m)])
                mt = Table(marks_table, colWidths=[3*inch, 2*inch])
                mt.setStyle(TableStyle([
                    ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
                    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
                ]))
                story.append(mt)
                story.append(Spacer(1, 12))

            remove_tmp = None
            if fig is not None:
                tmpfd = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmpname = tmpfd.name
                tmpfd.close()
                fig.savefig(tmpname, bbox_inches="tight")
                story.append(PDFImage(tmpname, width=6.5*inch, height=3.5*inch))
                story.append(Spacer(1, 12))
                remove_tmp = tmpname

            doc.build(story)

            if remove_tmp and os.path.exists(remove_tmp):
                try: os.remove(remove_tmp)
                except Exception: pass

            messagebox.showinfo("Success", f"Report saved to:\n{savep}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate PDF:\n{e}")

    # -------------------------
    # CSV export
    # -------------------------
    def export_csv(self):
        if not self.students:
            messagebox.showwarning("Warning", "No students to export.")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files","*.csv")], initialfile="students_export.csv")
        if not path:
            return
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["UID","Name","Class","Section","Subjects","Marks"])
                for s in self.students:
                    subjects = ";".join(s.subjects) if s.subjects else ""
                    marks = ";".join(s.marks) if s.marks else ""
                    writer.writerow([s.uid, s.name, s.student_class, s.section, subjects, marks])
            messagebox.showinfo("Exported", f"CSV exported to {path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export CSV:\n{e}")

    # -------------------------
    # Exit / cleanup
    # -------------------------
    def _on_exit(self):
        try:
            self.save_data()
            if hasattr(self, "conn") and self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
        finally:
            try:
                self.root.destroy()
            except Exception:
                pass

# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    if TB_AVAILABLE:
        root = tb.Window(themename="cosmo")
    else:
        root = tk.Tk()
    app = StudentManagementApp(root)
    root.mainloop()
