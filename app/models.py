from sqlalchemy import Column, BigInteger, Text, DateTime, ForeignKey, func, Integer, Boolean, JSON
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import relationship
from .db import Base

class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    user_id = Column(Integer, nullable=True)
    emp_no = Column(Text, nullable=True)
    company_id = Column(Integer, nullable=True)
    action = Column(Text, nullable=False)
    ip_address = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    severity = Column(Text, default='INFO')

class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    emp_no = Column(Text, nullable=False)
    ip_address = Column(Text, nullable=False)
    attempt_time = Column(DateTime(timezone=True), server_default=func.now())
    success = Column(Boolean, default=False)

class UserBlock(Base):
    __tablename__ = "user_blocks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    emp_no = Column(Text, nullable=False, unique=True)
    blocked_until = Column(DateTime(timezone=True), nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Session(Base):
    __tablename__ = "session"
    __table_args__ = {"schema": "instr"}
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Используем реальный тип БД instr.instr_type вместо Text,
    # чтобы драйвер не посылал ::VARCHAR и не падал с DatatypeMismatchError
    type = Column(
        ENUM(
            'vvodny', 'pervichny', 'povtorny', 'vneplanovy', 'celevoy',
            name='instr_type', schema='instr', create_type=False
        ),
        nullable=False,
    )   # vvodny/pervichny/povtorny/vneplanovy/celevoy
    month = Column(Text, nullable=False)  # 'YYYY-MM'
    file = Column(Text, nullable=False)   # filename with extension
    description = Column(Text)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    attendances = relationship("Attendance", backref="session", cascade="all,delete")

class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = {"schema": "instr"}
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, ForeignKey("instr.session.id", ondelete="CASCADE"))
    idnum = Column(Text, nullable=False)
    fio = Column(Text, nullable=False)
    company_id = Column(BigInteger)  # для уникальности: один табельный может быть в разных компаниях
    orgunit_id = Column(BigInteger)  # подразделение (цех/отдел)
    signed_at = Column(DateTime(timezone=True), server_default=func.now())
    worker_sig_path = Column(Text)
    instr_sig_path  = Column(Text)
    birthday        = Column(Text)
    profession      = Column(Text)
    cex             = Column(Text)
    instructor_name = Column(Text)

class InstructionalFile(Base):
    __tablename__ = "instructional_file"
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(Text, nullable=False, unique=True)
    file_name = Column(Text, nullable=False)
    file_type = Column(Text, nullable=False)  # vvodny/pervichny/povtorny/vneplanovy/celevoy
    company_id = Column(Integer, nullable=False)
    uploaded_by = Column(Text)
    uploaded_at = Column(DateTime(timezone=True), server_default=func.now())

class InstructionalQuestion(Base):
    __tablename__ = "instructional_questions"
    __table_args__ = {"schema": "instr"}
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(Text, nullable=False, unique=True)
    language = Column(Text, nullable=False, server_default='ru')  # 'ru' or 'kk'
    questions = Column(JSONB, nullable=False)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))
    version = Column(Integer, server_default='1')

class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = {"schema": "instr"}
    id = Column(Integer, primary_key=True, autoincrement=True)
    file_path = Column(Text, nullable=False)
    idnum = Column(Text, nullable=False)
    company_id = Column(Integer, nullable=False)
    language = Column(Text, nullable=False)
    questions_shown = Column(JSONB, nullable=False)
    answers_given = Column(JSONB, nullable=False)
    correct_count = Column(Integer, nullable=False)
    total_count = Column(Integer, nullable=False)
    score_percentage = Column(Integer)
    passed = Column(Boolean, nullable=False)
    attempted_at = Column(DateTime(timezone=True), server_default=func.now())
