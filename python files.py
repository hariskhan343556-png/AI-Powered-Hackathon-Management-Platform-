# ============================================
# BACKEND API - FastAPI Application
# ============================================

# main.py
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import uvicorn
import asyncio
import json
import os
from enum import Enum

app = FastAPI(title="HackSupport.pro API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# DATABASE MODELS (SQLAlchemy)
# ============================================

# models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean, JSON, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker
from sqlalchemy import create_engine
import enum

Base = declarative_base()

class UserRole(str, enum.Enum):
    ADMIN = "admin"
    HOST = "host"
    JUDGE = "judge"
    PARTICIPANT = "participant"
    SERVICE_PROVIDER = "service_provider"

class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    EVALUATED = "evaluated"
    WINNER = "winner"
    REJECTED = "rejected"

class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    role = Column(SQLEnum(UserRole))
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    hackathons_hosted = relationship("Hackathon", back_populates="host")
    judge_assignments = relationship("JudgeAssignment", back_populates="judge")
    projects = relationship("Project", back_populates="team_lead")
    tasks = relationship("Task", back_populates="assignee")

class Hackathon(Base):
    __tablename__ = "hackathons"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    description = Column(Text)
    host_id = Column(Integer, ForeignKey("users.id"))
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    max_teams = Column(Integer)
    current_teams = Column(Integer, default=0)
    status = Column(String, default="draft")  # draft, open, ongoing, completed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    config = Column(JSON, default={})
    
    # Relationships
    host = relationship("User", back_populates="hackathons_hosted")
    projects = relationship("Project", back_populates="hackathon")
    judge_assignments = relationship("JudgeAssignment", back_populates="hackathon")
    tasks = relationship("Task", back_populates="hackathon")

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    description = Column(Text)
    hackathon_id = Column(Integer, ForeignKey("hackathons.id"))
    team_lead_id = Column(Integer, ForeignKey("users.id"))
    team_members = Column(JSON, default=[])  # List of user IDs
    submission_url = Column(String)
    github_repo = Column(String)
    video_url = Column(String)
    status = Column(SQLEnum(ProjectStatus), default=ProjectStatus.DRAFT)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    hackathon = relationship("Hackathon", back_populates="projects")
    team_lead = relationship("User", back_populates="projects")
    evaluations = relationship("ProjectEvaluation", back_populates="project")
    scores = relationship("Score", back_populates="project")

class JudgeAssignment(Base):
    __tablename__ = "judge_assignments"
    id = Column(Integer, primary_key=True, index=True)
    judge_id = Column(Integer, ForeignKey("users.id"))
    hackathon_id = Column(Integer, ForeignKey("hackathons.id"))
    assigned_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    judge = relationship("User", back_populates="judge_assignments")
    hackathon = relationship("Hackathon", back_populates="judge_assignments")

class ProjectEvaluation(Base):
    __tablename__ = "project_evaluations"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    judge_id = Column(Integer, ForeignKey("users.id"))
    score = Column(Float)
    feedback = Column(Text)
    evaluation_date = Column(DateTime, default=datetime.utcnow)
    ai_generated = Column(Boolean, default=False)
    
    # Relationships
    project = relationship("Project", back_populates="evaluations")
    judge = relationship("User")

class Score(Base):
    __tablename__ = "scores"
    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"))
    criteria_name = Column(String)
    score = Column(Float)
    weight = Column(Float, default=1.0)
    judge_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    project = relationship("Project", back_populates="scores")
    judge = relationship("User")

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    description = Column(Text)
    hackathon_id = Column(Integer, ForeignKey("hackathons.id"))
    assignee_id = Column(Integer, ForeignKey("users.id"))
    created_by = Column(Integer, ForeignKey("users.id"))
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.PENDING)
    priority = Column(String, default="medium")  # low, medium, high, critical
    due_date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    dependencies = Column(JSON, default=[])  # List of task IDs
    
    # Relationships
    hackathon = relationship("Hackathon", back_populates="tasks")
    assignee = relationship("User", foreign_keys=[assignee_id], back_populates="tasks")

# ============================================
# AUTHENTICATION & SECURITY
# ============================================

# auth.py
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

SECRET_KEY = "your-secret-key-here-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def require_role(required_role: UserRole):
    async def role_checker(current_user: User = Depends(get_current_active_user)):
        if current_user.role != required_role and current_user.role != UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return role_checker

# ============================================
# AI ENGINE - JudgeGPT & HostGPT
# ============================================

# ai_engine.py
import openai
from typing import Dict, List, Any
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import re

class JudgeGPT:
    """AI-powered judging system for hackathon projects"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        openai.api_key = self.api_key
        self.vectorizer = TfidfVectorizer(max_features=1000)
        
    async def evaluate_project(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate a project using AI and return scores with feedback"""
        
        # Extract project details
        name = project_data.get("name", "")
        description = project_data.get("description", "")
        github_repo = project_data.get("github_repo", "")
        
        # Build comprehensive project context
        context = f"""
        Project Name: {name}
        Description: {description}
        GitHub Repository: {github_repo}
        """
        
        # Define scoring criteria
        criteria = {
            "innovation": 0.0,
            "feasibility": 0.0,
            "design": 0.0,
            "impact": 0.0,
            "technical_complexity": 0.0,
            "code_quality": 0.0,
            "team_collaboration": 0.0,
            "presentation": 0.0
        }
        
        # Use OpenAI for detailed evaluation
        try:
            response = await self._get_ai_evaluation(context)
            ai_results = self._parse_ai_response(response)
            
            # Merge with criteria weights
            for key in criteria:
                if key in ai_results:
                    criteria[key] = min(100, max(0, ai_results[key]))
                    
            # Calculate overall score
            weights = {
                "innovation": 0.25,
                "feasibility": 0.20,
                "design": 0.15,
                "impact": 0.20,
                "technical_complexity": 0.10,
                "code_quality": 0.05,
                "team_collaboration": 0.03,
                "presentation": 0.02
            }
            
            overall_score = sum(criteria[key] * weights.get(key, 0.1) for key in criteria)
            
            # Generate comprehensive feedback
            feedback = await self._generate_feedback(project_data, criteria)
            
            return {
                "scores": criteria,
                "overall_score": round(overall_score, 2),
                "feedback": feedback,
                "detailed_analysis": ai_results.get("analysis", ""),
                "ai_confidence": ai_results.get("confidence", 0.85),
                "improvement_suggestions": ai_results.get("suggestions", [])
            }
            
        except Exception as e:
            # Fallback to rule-based scoring
            return self._fallback_evaluation(project_data)
    
    async def _get_ai_evaluation(self, context: str) -> str:
        """Get AI evaluation from OpenAI"""
        prompt = f"""
        Evaluate the following hackathon project. Provide scores (0-100) for:
        - innovation (novelty and creativity)
        - feasibility (practical implementation)
        - design (user experience and aesthetics)
        - impact (potential value and reach)
        - technical_complexity (technical challenge)
        - code_quality (if code is available)
        - team_collaboration (if mentioned)
        - presentation (clarity and communication)
        
        Also provide:
        - Confidence score (0-1)
        - Key strengths
        - Areas for improvement
        - Overall assessment
        
        Project Context:
        {context}
        
        Return as JSON.
        """
        
        try:
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert hackathon judge."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            return response.choices[0].message.content
        except:
            return self._fallback_ai_response()
    
    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        """Parse AI response into structured data"""
        try:
            # Try to parse as JSON
            import json
            data = json.loads(response)
            return data
        except:
            # Fallback parsing
            result = {"confidence": 0.7, "analysis": response[:500]}
            
            # Extract scores using regex
            score_patterns = {
                "innovation": r"innovation.*?(\d+)",
                "feasibility": r"feasibility.*?(\d+)",
                "design": r"design.*?(\d+)",
                "impact": r"impact.*?(\d+)",
                "technical_complexity": r"technical.*?(\d+)",
                "code_quality": r"code.*?(\d+)"
            }
            
            for key, pattern in score_patterns.items():
                match = re.search(pattern, response, re.IGNORECASE)
                if match:
                    result[key] = float(match.group(1))
                else:
                    result[key] = 50.0
                    
            return result
    
    def _fallback_evaluation(self, project_data: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based fallback evaluation"""
        description = project_data.get("description", "")
        name = project_data.get("name", "")
        
        # Simple rule-based scoring
        base_score = 50
        innovation_bonus = min(30, len(description.split()) * 0.1)
        tech_bonus = min(20, len(project_data.get("github_repo", "")) * 2)
        
        scores = {
            "innovation": min(100, base_score + innovation_bonus),
            "feasibility": min(100, base_score + 10),
            "design": min(100, base_score + 5),
            "impact": min(100, base_score + 15),
            "technical_complexity": min(100, base_score + tech_bonus),
            "code_quality": min(100, 60),
            "team_collaboration": 70,
            "presentation": 65
        }
        
        overall = sum(scores.values()) / len(scores)
        
        return {
            "scores": scores,
            "overall_score": round(overall, 2),
            "feedback": "Evaluation completed using rule-based system.",
            "ai_confidence": 0.5,
            "improvement_suggestions": [
                "Consider adding more detailed documentation",
                "Include a demo video if possible",
                "Clarify the problem statement"
            ]
        }
    
    def _fallback_ai_response(self) -> str:
        return json.dumps({
            "innovation": 70,
            "feasibility": 65,
            "design": 60,
            "impact": 72,
            "technical_complexity": 55,
            "code_quality": 58,
            "team_collaboration": 50,
            "presentation": 45,
            "confidence": 0.6,
            "analysis": "Project shows potential but needs refinement.",
            "suggestions": ["Improve documentation", "Add more features"]
        })
    
    async def _generate_feedback(self, project_data: Dict, scores: Dict) -> str:
        """Generate detailed feedback based on scores"""
        feedback_parts = []
        
        if scores["innovation"] > 80:
            feedback_parts.append("🌟 Excellent innovation! Your project brings fresh ideas.")
        elif scores["innovation"] < 50:
            feedback_parts.append("💡 Consider adding more novel elements to stand out.")
            
        if scores["feasibility"] > 80:
            feedback_parts.append("✅ Highly feasible implementation with clear path to completion.")
        elif scores["feasibility"] < 50:
            feedback_parts.append("⚠️ Feasibility needs improvement. Consider simplifying the scope.")
            
        if scores["design"] > 80:
            feedback_parts.append("🎨 Great design! User experience is well thought out.")
        elif scores["design"] < 50:
            feedback_parts.append("🖌️ Design could be more polished. Consider user testing.")
            
        if scores["impact"] > 80:
            feedback_parts.append("🌍 High potential impact! This project could make a real difference.")
        elif scores["impact"] < 50:
            feedback_parts.append("📊 Impact is unclear. Define your target audience clearly.")
            
        return " ".join(feedback_parts)

class HostGPT:
    """AI-powered hackathon management and coordination system"""
    
    def __init__(self):
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        openai.api_key = self.openai_api_key
        
    async def optimize_schedule(self, hackathon_data: Dict[str, Any]) -> Dict[str, Any]:
        """Optimize hackathon schedule using AI"""
        tasks = hackathon_data.get("tasks", [])
        resources = hackathon_data.get("resources", {})
        constraints = hackathon_data.get("constraints", {})
        
        # Use AI to optimize schedule
        try:
            prompt = f"""
            Optimize this hackathon schedule with:
            Tasks: {json.dumps(tasks)}
            Resources: {json.dumps(resources)}
            Constraints: {json.dumps(constraints)}
            
            Provide optimized schedule, resource allocation, and risk mitigation.
            """
            
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an expert hackathon project manager."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            
            result = self._parse_schedule_response(response.choices[0].message.content)
            return result
            
        except Exception as e:
            return self._fallback_schedule_optimization(tasks)
    
    def _parse_schedule_response(self, response: str) -> Dict[str, Any]:
        try:
            import json
            data = json.loads(response)
            return data
        except:
            return {
                "schedule": [],
                "resource_allocation": {},
                "risks": [],
                "recommendations": []
            }
    
    def _fallback_schedule_optimization(self, tasks: List) -> Dict[str, Any]:
        """Simple fallback scheduling"""
        return {
            "schedule": tasks,
            "resource_allocation": {"team": "default"},
            "risks": [],
            "recommendations": ["Consider parallelizing tasks"]
        }
    
    async def assign_tasks_ai(self, tasks: List[Dict], team_members: List[Dict]) -> List[Dict]:
        """Intelligent task assignment based on skills and availability"""
        try:
            prompt = f"""
            Assign these tasks to team members optimally:
            Tasks: {json.dumps(tasks)}
            Team Members: {json.dumps(team_members)}
            
            Consider skills, workload, and dependencies.
            """
            
            response = openai.ChatCompletion.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an AI task assignment specialist."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            
            result = self._parse_assignment_response(response.choices[0].message.content)
            return result
            
        except:
            return self._fallback_assignment(tasks, team_members)
    
    def _parse_assignment_response(self, response: str) -> List[Dict]:
        try:
            import json
            data = json.loads(response)
            return data.get("assignments", [])
        except:
            return []
    
    def _fallback_assignment(self, tasks: List[Dict], team_members: List[Dict]) -> List[Dict]:
        """Simple round-robin assignment"""
        assignments = []
        for i, task in enumerate(tasks):
            member = team_members[i % len(team_members)]
            task["assignee_id"] = member.get("id")
            assignments.append(task)
        return assignments

# ============================================
# API ROUTES
# ============================================

# routes/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import timedelta

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])

@auth_router.post("/register")
async def register(user_data: dict, db: Session = Depends(get_db)):
    """Register a new user"""
    # Check if user exists
    existing_user = db.query(User).filter(
        (User.username == user_data.get("username")) | 
        (User.email == user_data.get("email"))
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered"
        )
    
    # Create new user
    hashed_password = get_password_hash(user_data.get("password"))
    new_user = User(
        username=user_data.get("username"),
        email=user_data.get("email"),
        hashed_password=hashed_password,
        full_name=user_data.get("full_name"),
        role=UserRole(user_data.get("role", "participant"))
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {"message": "User created successfully", "user_id": new_user.id}

@auth_router.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """Login and get access token"""
    user = db.query(User).filter(User.username == form_data.username).first()
    
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username, "role": user.role.value},
        expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role.value,
            "full_name": user.full_name
        }
    }

@auth_router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """Get current user information"""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role.value,
        "full_name": current_user.full_name,
        "created_at": current_user.created_at.isoformat()
    }

# ============================================
# HACKATHON ROUTES
# ============================================

# routes/hackathons.py
hackathon_router = APIRouter(prefix="/hackathons", tags=["Hackathons"])

@hackathon_router.post("/")
async def create_hackathon(
    hackathon_data: dict,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role(UserRole.HOST)),
    db: Session = Depends(get_db)
):
    """Create a new hackathon"""
    
    new_hackathon = Hackathon(
        title=hackathon_data.get("title"),
        description=hackathon_data.get("description"),
        host_id=current_user.id,
        start_date=datetime.fromisoformat(hackathon_data.get("start_date")),
        end_date=datetime.fromisoformat(hackathon_data.get("end_date")),
        max_teams=hackathon_data.get("max_teams", 50),
        config=hackathon_data.get("config", {})
    )
    
    db.add(new_hackathon)
    db.commit()
    db.refresh(new_hackathon)
    
    # Trigger HostGPT for initial setup
    background_tasks.add_task(initialize_hackathon_ai, new_hackathon.id)
    
    return {
        "message": "Hackathon created successfully",
        "hackathon_id": new_hackathon.id
    }

async def initialize_hackathon_ai(hackathon_id: int):
    """Background task for AI initialization"""
    # This would use HostGPT to set up tasks, schedules, etc.
    pass

@hackathon_router.get("/")
async def list_hackathons(
    status: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """List all hackathons (filtered by status)"""
    query = db.query(Hackathon)
    
    if status:
        query = query.filter(Hackathon.status == status)
    
    if current_user.role == UserRole.HOST:
        query = query.filter(Hackathon.host_id == current_user.id)
    
    hackathons = query.all()
    
    return [{
        "id": h.id,
        "title": h.title,
        "description": h.description,
        "status": h.status,
        "start_date": h.start_date.isoformat(),
        "end_date": h.end_date.isoformat(),
        "current_teams": h.current_teams,
        "max_teams": h.max_teams,
        "host_id": h.host_id
    } for h in hackathons]

@hackathon_router.get("/{hackathon_id}")
async def get_hackathon(
    hackathon_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get hackathon details by ID"""
    hackathon = db.query(Hackathon).filter(Hackathon.id == hackathon_id).first()
    
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")
    
    return {
        "id": hackathon.id,
        "title": hackathon.title,
        "description": hackathon.description,
        "host": {
            "id": hackathon.host.id,
            "username": hackathon.host.username,
            "full_name": hackathon.host.full_name
        },
        "start_date": hackathon.start_date.isoformat(),
        "end_date": hackathon.end_date.isoformat(),
        "status": hackathon.status,
        "current_teams": hackathon.current_teams,
        "max_teams": hackathon.max_teams,
        "config": hackathon.config,
        "created_at": hackathon.created_at.isoformat()
    }

@hackathon_router.put("/{hackathon_id}")
async def update_hackathon(
    hackathon_id: int,
    update_data: dict,
    current_user: User = Depends(require_role(UserRole.HOST)),
    db: Session = Depends(get_db)
):
    """Update hackathon details"""
    hackathon = db.query(Hackathon).filter(Hackathon.id == hackathon_id).first()
    
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")
    
    if hackathon.host_id != current_user.id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not authorized to update this hackathon")
    
    # Update fields
    for key, value in update_data.items():
        if hasattr(hackathon, key):
            setattr(hackathon, key, value)
    
    hackathon.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(hackathon)
    
    return {"message": "Hackathon updated successfully"}

# ============================================
# JUDGING ROUTES
# ============================================

# routes/judging.py
judging_router = APIRouter(prefix="/judging", tags=["Judging"])

@judging_router.post("/evaluate/{project_id}")
async def evaluate_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_role(UserRole.JUDGE)),
    db: Session = Depends(get_db)
):
    """Evaluate a project using JudgeGPT"""
    
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Check if judge is assigned to this project's hackathon
    assignment = db.query(JudgeAssignment).filter(
        JudgeAssignment.judge_id == current_user.id,
        JudgeAssignment.hackathon_id == project.hackathon_id
    ).first()
    
    if not assignment and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Not assigned to judge this hackathon")
    
    # Trigger AI evaluation in background
    background_tasks.add_task(perform_ai_evaluation, project_id, current_user.id)
    
    return {"message": "Evaluation started", "project_id": project_id}

async def perform_ai_evaluation(project_id: int, judge_id: int):
    """Background task for AI evaluation"""
    # Implementation would use JudgeGPT
    pass

@judging_router.get("/results/{hackathon_id}")
async def get_judging_results(
    hackathon_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get judging results for a hackathon"""
    
    hackathon = db.query(Hackathon).filter(Hackathon.id == hackathon_id).first()
    if not hackathon:
        raise HTTPException(status_code=404, detail="Hackathon not found")
    
    # Check permissions
    if current_user.role not in [UserRole.ADMIN, UserRole.HOST] and hackathon.host_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view results")
    
    # Get all projects with evaluations
    projects = db.query(Project).filter(Project.hackathon_id == hackathon_id).all()
    
    results = []
    for project in projects:
        evaluations = db.query(ProjectEvaluation).filter(
            ProjectEvaluation.project_id == project.id
        ).all()
        
        if evaluations:
            avg_score = sum(e.score for e in evaluations) / len(evaluations)
            results.append({
                "project_id": project.id,
                "project_name": project.name,
                "average_score": round(avg_score, 2),
                "evaluation_count": len(evaluations),
                "evaluations": [
                    {
                        "judge_id": e.judge_id,
                        "score": e.score,
                        "feedback": e.feedback[:100] + "..." if len(e.feedback) > 100 else e.feedback
                    }
                    for e in evaluations
                ]
            })
    
    # Sort by average score
    results.sort(key=lambda x: x["average_score"], reverse=True)
    
    return {
        "hackathon_id": hackathon_id,
        "hackathon_title": hackathon.title,
        "total_projects": len(projects),
        "results": results
    }

# ============================================
# TASK MANAGEMENT ROUTES
# ============================================

# routes/tasks.py
task_router = APIRouter(prefix="/tasks", tags=["Tasks"])

@task_router.post("/")
async def create_task(
    task_data: dict,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create a new task (HostGPT will optimize assignment)"""
    
    new_task = Task(
        title=task_data.get("title"),
        description=task_data.get("description"),
        hackathon_id=task_data.get("hackathon_id"),
        created_by=current_user.id,
        priority=task_data.get("priority", "medium"),
        due_date=datetime.fromisoformat(task_data.get("due_date")) if task_data.get("due_date") else None,
        dependencies=task_data.get("dependencies", [])
    )
    
    db.add(new_task)
    db.commit()
    db.refresh(new_task)
    
    # HostGPT will automatically assign tasks
    await auto_assign_task(new_task.id, db)
    
    return {
        "message": "Task created",
        "task_id": new_task.id,
        "assignee_id": new_task.assignee_id
    }

async def auto_assign_task(task_id: int, db: Session):
    """Use HostGPT to intelligently assign tasks"""
    # This would use HostGPT to find the best assignee
    pass

@task_router.get("/hackathon/{hackathon_id}")
async def get_hackathon_tasks(
    hackathon_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get all tasks for a hackathon"""
    
    tasks = db.query(Task).filter(Task.hackathon_id == hackathon_id).all()
    
    return [{
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "status": t.status.value,
        "priority": t.priority,
        "assignee": {
            "id": t.assignee.id,
            "username": t.assignee.username
        } if t.assignee else None,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "dependencies": t.dependencies,
        "created_at": t.created_at.isoformat()
    } for t in tasks]

# ============================================
# ANALYTICS & REPORTING
# ============================================

# routes/analytics.py
analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"])

@analytics_router.get("/hackathon/{hackathon_id}")
async def get_hackathon_analytics(
    hackathon_id: int,
    current_user: User = Depends(require_role(UserRole.HOST)),
    db: Session = Depends(get_db)
):
    """Get comprehensive analytics for