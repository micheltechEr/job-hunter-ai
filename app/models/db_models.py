from sqlalchemy import Column, Integer, String, Text, ForeignKey, DateTime, Boolean, JSON, Float
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func
import datetime

Base = declarative_base()

class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    education = Column(Text, nullable=True)
    desired_roles = Column(JSON, nullable=True)  # List of strings e.g. ["Backend Developer"]
    technologies = Column(JSON, nullable=True)  # List of strings e.g. ["Python", "Docker"]
    languages = Column(JSON, nullable=True)      # List of strings
    cloud_providers = Column(JSON, nullable=True) # List of strings
    devops_tools = Column(JSON, nullable=True)    # List of strings
    databases = Column(JSON, nullable=True)       # List of strings
    certifications = Column(JSON, nullable=True)  # List of strings
    location = Column(String(255), nullable=True)
    work_mode = Column(String(50), nullable=True) # Remote, Hybrid, On-site
    professional_goals = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    experiences = relationship("Experience", back_populates="user", cascade="all, delete-orphan", lazy="selectin")
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan", lazy="selectin")


class Experience(Base):
    __tablename__ = "experiences"

    id = Column(Integer, primary_key=True, index=True)
    user_profile_id = Column(Integer, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    company = Column(String(255), nullable=False)
    role = Column(String(255), nullable=False)
    start_date = Column(String(50), nullable=True)
    end_date = Column(String(50), nullable=True)
    description = Column(Text, nullable=True)
    skills_used = Column(JSON, nullable=True)

    user = relationship("UserProfile", back_populates="experiences", lazy="selectin")


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    user_profile_id = Column(Integer, ForeignKey("user_profiles.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    technologies = Column(JSON, nullable=True)
    url = Column(String(512), nullable=True)

    user = relationship("UserProfile", back_populates="projects", lazy="selectin")


class Resume(Base):
    __tablename__ = "resumes"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    version_name = Column(String(100), nullable=False) # e.g. "backend", "cloud", "devops"
    file_hash = Column(String(64), nullable=False)
    parsed_data = Column(JSON, nullable=True) # Structued CV parsed data
    embedding = Column(JSON, nullable=True) # Fallback vector array (float list)
    created_at = Column(DateTime, default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    company = Column(String(255), nullable=False)
    url = Column(String(1024), nullable=True)
    description = Column(Text, nullable=False)
    location = Column(String(255), nullable=True)
    work_mode = Column(String(100), nullable=True)
    salary = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())

    analysis = relationship("JobAnalysis", uselist=False, back_populates="job", cascade="all, delete-orphan", lazy="selectin")
    application = relationship("Application", uselist=False, back_populates="job", cascade="all, delete-orphan", lazy="selectin")


class JobAnalysis(Base):
    __tablename__ = "job_analyses"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    extracted_role = Column(String(255), nullable=True)
    seniority = Column(String(100), nullable=True)
    location = Column(String(255), nullable=True)
    work_mode = Column(String(100), nullable=True)
    required_skills = Column(JSON, nullable=True) # List of requirements
    nice_to_have = Column(JSON, nullable=True)    # List of desired
    responsibilities = Column(JSON, nullable=True)
    education = Column(JSON, nullable=True)
    languages = Column(JSON, nullable=True)
    experience_required = Column(String(255), nullable=True)
    raw_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=func.now())

    job = relationship("Job", back_populates="analysis", lazy="selectin")


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), unique=True, nullable=False)
    resume_id = Column(Integer, ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True)
    recipient_email = Column(String(255), nullable=True)
    score = Column(Integer, nullable=True)
    fit = Column(String(50), nullable=True) # IGNORE, REVIEW, GOOD_MATCH, HIGH_MATCH
    email_subject = Column(String(512), nullable=True)
    email_body = Column(Text, nullable=True)
    status = Column(String(50), default="DISCOVERED") # DISCOVERED, ANALYZED, REVIEW, APPROVED, SENT, REPLIED, etc.
    created_at = Column(DateTime, default=func.now())
    sent_at = Column(DateTime, nullable=True)
    response_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    job = relationship("Job", back_populates="application", lazy="selectin")
    resume = relationship("Resume", lazy="selectin")


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, index=True)
    email_address = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
