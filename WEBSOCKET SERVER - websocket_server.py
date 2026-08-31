# ============================================
# WEBSOCKET SERVER - websocket_server.py
# ============================================

import asyncio
import json
import logging
from typing import Dict, Set, Any
from fastapi import WebSocket, WebSocketDisconnect, APIRouter, Depends
from datetime import datetime
import redis.asyncio as redis
from sqlalchemy.orm import Session

websocket_router = APIRouter()
logger = logging.getLogger(__name__)

class ConnectionManager:
    """Manage WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.user_connections: Dict[str, WebSocket] = {}
        self.redis_client = None
        
    async def initialize_redis(self):
        """Initialize Redis connection for pub/sub"""
        self.redis_client = await redis.from_url(
            "redis://localhost:6379",
            decode_responses=True
        )
        # Subscribe to channels
        asyncio.create_task(self._listen_to_redis())
        
    async def _listen_to_redis(self):
        """Listen to Redis channels for cross-instance communication"""
        pubsub = self.redis_client.pubsub()
        await pubsub.subscribe(
            "hackathon_updates",
            "judging_updates",
            "task_updates",
            "system_notifications"
        )
        
        async for message in pubsub.listen():
            if message['type'] == 'message':
                channel = message['channel']
                data = json.loads(message['data'])
                await self.broadcast_to_channel(channel, data)
    
    async def connect(self, websocket: WebSocket, user_id: str):
        """Accept and store WebSocket connection"""
        await websocket.accept()
        
        # Store user connection
        self.user_connections[user_id] = websocket
        
        # Add to default channel
        if "default" not in self.active_connections:
            self.active_connections["default"] = set()
        self.active_connections["default"].add(websocket)
        
        logger.info(f"User {user_id} connected")
        
        # Send connection confirmation
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        })
        
    def disconnect(self, websocket: WebSocket, user_id: str):
        """Remove WebSocket connection"""
        if user_id in self.user_connections:
            del self.user_connections[user_id]
            
        for channel in self.active_connections:
            if websocket in self.active_connections[channel]:
                self.active_connections[channel].remove(websocket)
                
        logger.info(f"User {user_id} disconnected")
    
    async def send_personal_message(self, message: dict, user_id: str):
        """Send message to specific user"""
        if user_id in self.user_connections:
            try:
                await self.user_connections[user_id].send_json(message)
                return True
            except:
                return False
        return False
    
    async def broadcast_to_channel(self, channel: str, message: dict):
        """Broadcast message to all users in a channel"""
        if channel in self.active_connections:
            for connection in self.active_connections[channel]:
                try:
                    await connection.send_json(message)
                except:
                    pass
    
    async def broadcast_to_hackathon(self, hackathon_id: int, message: dict):
        """Broadcast message to all users in a hackathon"""
        channel = f"hackathon_{hackathon_id}"
        if channel in self.active_connections:
            for connection in self.active_connections[channel]:
                try:
                    await connection.send_json(message)
                except:
                    pass
    
    async def publish_to_redis(self, channel: str, data: dict):
        """Publish message to Redis for cross-instance communication"""
        if self.redis_client:
            await self.redis_client.publish(
                channel,
                json.dumps(data)
            )

manager = ConnectionManager()

@websocket_router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """WebSocket endpoint for real-time communication"""
    await manager.connect(websocket, user_id)
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message = json.loads(data)
            
            # Handle different message types
            message_type = message.get('type', 'unknown')
            
            if message_type == 'subscribe':
                # Subscribe to a channel
                channel = message.get('channel')
                if channel:
                    if channel not in manager.active_connections:
                        manager.active_connections[channel] = set()
                    manager.active_connections[channel].add(websocket)
                    
                    await websocket.send_json({
                        "type": "subscription",
                        "channel": channel,
                        "status": "subscribed"
                    })
                    
            elif message_type == 'unsubscribe':
                channel = message.get('channel')
                if channel and channel in manager.active_connections:
                    manager.active_connections[channel].discard(websocket)
                    
                    await websocket.send_json({
                        "type": "subscription",
                        "channel": channel,
                        "status": "unsubscribed"
                    })
                    
            elif message_type == 'message':
                # Handle chat message
                target = message.get('target')
                content = message.get('content')
                
                if target == 'hackathon':
                    hackathon_id = message.get('hackathon_id')
                    await manager.broadcast_to_hackathon(hackathon_id, {
                        "type": "chat_message",
                        "user_id": user_id,
                        "content": content,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
                elif target == 'user':
                    target_user = message.get('target_user_id')
                    await manager.send_personal_message({
                        "type": "chat_message",
                        "user_id": user_id,
                        "content": content,
                        "timestamp": datetime.utcnow().isoformat()
                    }, target_user)
                    
            elif message_type == 'typing':
                # Send typing indicator
                target = message.get('target')
                if target == 'hackathon':
                    hackathon_id = message.get('hackathon_id')
                    await manager.broadcast_to_hackathon(hackathon_id, {
                        "type": "typing",
                        "user_id": user_id,
                        "timestamp": datetime.utcnow().isoformat()
                    })
                    
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, user_id)

# ============================================
# MICROSERVICES - microservices.py
# ============================================

import asyncio
import aiohttp
from typing import Dict, Any
from celery import Celery
import redis
import json
from datetime import datetime

# Celery configuration for task queue
celery_app = Celery(
    'hacksupport',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0'
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
)

@celery_app.task(bind=True, max_retries=3)
def evaluate_project_async(self, project_id: int, hackathon_id: int):
    """Asynchronous task for AI project evaluation"""
    try:
        # Import AI engine
        from ai_engine import JudgeGPT
        
        # Get project data from database
        # This would fetch from DB
        project_data = {
            'id': project_id,
            'name': f'Project_{project_id}',
            'description': 'Sample project description',
            'github_repo': 'https://github.com/sample/project'
        }
        
        # Run AI evaluation
        judge = JudgeGPT()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            judge.evaluate_project(project_data)
        )
        loop.close()
        
        # Save results to database
        # This would update the ProjectEvaluation table
        
        # Send WebSocket notification
        asyncio.run(send_evaluation_notification(project_id, result))
        
        return {
            'project_id': project_id,
            'status': 'completed',
            'score': result['overall_score'],
            'feedback': result['feedback']
        }
        
    except Exception as e:
        # Retry on failure
        self.retry(exc=e, countdown=60)
        return {
            'project_id': project_id,
            'status': 'failed',
            'error': str(e)
        }

@celery_app.task
def generate_weekly_report(hackathon_id: int):
    """Generate weekly hackathon report"""
    try:
        # Fetch data from database
        # Generate report
        report = {
            'hackathon_id': hackathon_id,
            'period': 'weekly',
            'generated_at': datetime.utcnow().isoformat(),
            'statistics': {
                'total_projects': 25,
                'evaluations': 18,
                'average_score': 72.5,
                'top_performers': [
                    {'project': 'EcoChain', 'score': 94},
                    {'project': 'MediAssist', 'score': 88}
                ]
            },
            'insights': [
                'Innovation scores have increased by 15% this week',
                'Technical complexity remains the biggest challenge',
                'Team collaboration is consistently strong'
            ]
        }
        
        # Save report to database
        # Send email notification
        return report
        
    except Exception as e:
        return {
            'status': 'failed',
            'error': str(e)
        }

@celery_app.task
def optimize_hackathon_schedule(hackathon_id: int):
    """Use HostGPT to optimize hackathon schedule"""
    try:
        from ai_engine import HostGPT
        host = HostGPT()
        
        # Get tasks and resources from database
        tasks = [
            {'id': 1, 'title': 'Setup venue', 'duration': 4, 'resources': ['venue']},
            {'id': 2, 'title': 'Registration', 'duration': 3, 'resources': ['staff']},
            {'id': 3, 'title': 'Opening ceremony', 'duration': 2, 'resources': ['stage']},
            {'id': 4, 'title': 'Hacking session', 'duration': 24, 'resources': ['workspace']},
            {'id': 5, 'title': 'Judging', 'duration': 6, 'resources': ['judges', 'venue']},
            {'id': 6, 'title': 'Awards ceremony', 'duration': 3, 'resources': ['stage', 'venue']},
        ]
        
        resources = {
            'venue': 2,
            'staff': 10,
            'judges': 5,
            'stage': 1,
            'workspace': 50
        }
        
        constraints = {
            'max_parallel_tasks': 3,
            'working_hours': [9, 22]
        }
        
        # Run optimization
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(
            host.optimize_schedule({
                'tasks': tasks,
                'resources': resources,
                'constraints': constraints
            })
        )
        loop.close()
        
        return result
        
    except Exception as e:
        return {
            'status': 'failed',
            'error': str(e)
        }

# ============================================
# MICROSERVICE - Notification Service
# ============================================

class NotificationService:
    """Handle all notifications across the platform"""
    
    def __init__(self):
        self.email_client = None
        self.sms_client = None
        self.push_client = None
        
    async def send_email(self, to: str, subject: str, body: str, html: str = None):
        """Send email notification"""
        # Implementation would use SendGrid, AWS SES, etc.
        print(f"Email sent to {to}: {subject}")
        return {'status': 'sent', 'to': to}
    
    async def send_sms(self, to: str, message: str):
        """Send SMS notification"""
        # Implementation would use Twilio or similar
        print(f"SMS sent to {to}: {message[:50]}...")
        return {'status': 'sent', 'to': to}
    
    async def send_push_notification(self, user_id: str, title: str, body: str, data: dict = None):
        """Send push notification"""
        # Implementation would use Firebase Cloud Messaging
        print(f"Push notification sent to {user_id}: {title}")
        return {'status': 'sent', 'user_id': user_id}
    
    async def send_judging_notification(self, project_id: int, score: float, feedback: str):
        """Send notification about judging results"""
        # Get project details from database
        project = {'name': f'Project_{project_id}', 'team_lead': 'user@example.com'}
        
        await self.send_email(
            to=project['team_lead'],
            subject=f"Your hackathon project has been evaluated!",
            body=f"""
            Your project '{project['name']}' has been evaluated.
            Score: {score}/100
            Feedback: {feedback}
            """
        )
        
        return {'status': 'sent'}

# ============================================
# DATABASE MIGRATIONS - migrations.py
# ============================================

from alembic import op
import sqlalchemy as sa
from alembic import context

def upgrade():
    """Apply database migrations"""
    
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('full_name', sa.String(), nullable=True),
        sa.Column('role', sa.Enum('admin', 'host', 'judge', 'participant', 'service_provider'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    
    # Create hackathons table
    op.create_table(
        'hackathons',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.DateTime(), nullable=False),
        sa.Column('end_date', sa.DateTime(), nullable=False),
        sa.Column('max_teams', sa.Integer(), nullable=False),
        sa.Column('current_teams', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('config', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['host_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_hackathons_id'), 'hackathons', ['id'], unique=False)
    op.create_index(op.f('ix_hackathons_title'), 'hackathons', ['title'], unique=False)
    
    # Create projects table
    op.create_table(
        'projects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('hackathon_id', sa.Integer(), nullable=False),
        sa.Column('team_lead_id', sa.Integer(), nullable=False),
        sa.Column('team_members', sa.JSON(), nullable=True),
        sa.Column('submission_url', sa.String(), nullable=True),
        sa.Column('github_repo', sa.String(), nullable=True),
        sa.Column('video_url', sa.String(), nullable=True),
        sa.Column('status', sa.Enum('draft', 'submitted', 'under_review', 'evaluated', 'winner', 'rejected'), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['hackathon_id'], ['hackathons.id'], ),
        sa.ForeignKeyConstraint(['team_lead_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_id'), 'projects', ['id'], unique=False)
    op.create_index(op.f('ix_projects_name'), 'projects', ['name'], unique=False)
    
    # Create judge_assignments table
    op.create_table(
        'judge_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('judge_id', sa.Integer(), nullable=False),
        sa.Column('hackathon_id', sa.Integer(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['judge_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['hackathon_id'], ['hackathons.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create project_evaluations table
    op.create_table(
        'project_evaluations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('judge_id', sa.Integer(), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('feedback', sa.Text(), nullable=True),
        sa.Column('evaluation_date', sa.DateTime(), nullable=False),
        sa.Column('ai_generated', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['judge_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create tasks table
    op.create_table(
        'tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('hackathon_id', sa.Integer(), nullable=False),
        sa.Column('assignee_id', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('status', sa.Enum('pending', 'in_progress', 'completed', 'blocked'), nullable=False),
        sa.Column('priority', sa.String(), nullable=False),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('dependencies', sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(['hackathon_id'], ['hackathons.id'], ),
        sa.ForeignKeyConstraint(['assignee_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create scores table
    op.create_table(
        'scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('criteria_name', sa.String(), nullable=False),
        sa.Column('score', sa.Float(), nullable=False),
        sa.Column('weight', sa.Float(), nullable=False),
        sa.Column('judge_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['judge_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    """Rollback database migrations"""
    op.drop_table('scores')
    op.drop_table('tasks')
    op.drop_table('project_evaluations')
    op.drop_table('judge_assignments')
    op.drop_table('projects')
    op.drop_table('hackathons')
    op.drop_table('users')
    
    # Drop enum types
    op.execute('DROP TYPE IF EXISTS userrole')
    op.execute('DROP TYPE IF EXISTS projectstatus')
    op.execute('DROP TYPE IF EXISTS taskstatus')

# ============================================
# TESTING SUITE - test_ai_engine.py
# ============================================

import pytest
import asyncio
from unittest.mock import Mock, patch
from ai_engine import JudgeGPT, HostGPT
from fastapi.testclient import TestClient
from main import app

class TestJudgeGPT:
    """Test cases for JudgeGPT AI engine"""
    
    @pytest.fixture
    def judge(self):
        return JudgeGPT()
    
    @pytest.mark.asyncio
    async def test_evaluate_project(self, judge):
        """Test project evaluation functionality"""
        project_data = {
            'name': 'Test Project',
            'description': 'A test project for hackathon',
            'github_repo': 'https://github.com/test/project'
        }
        
        result = await judge.evaluate_project(project_data)
        
        assert 'scores' in result
        assert 'overall_score' in result
        assert 'feedback' in result
        assert 'ai_confidence' in result
        assert 'improvement_suggestions' in result
        
        # Check score ranges
        assert 0 <= result['overall_score'] <= 100
        assert all(0 <= v <= 100 for v in result['scores'].values())
        
    @pytest.mark.asyncio
    async def test_fallback_evaluation(self, judge):
        """Test fallback evaluation when AI fails"""
        project_data = {
            'name': 'Fallback Project',
            'description': 'Test fallback mechanism'
        }
        
        with patch('ai_engine.openai.ChatCompletion.create', side_effect=Exception('API Error')):
            result = await judge.evaluate_project(project_data)
            
            assert 'scores' in result
            assert 'overall_score' in result
            assert 'ai_confidence' in result
            assert result['ai_confidence'] == 0.5  # Fallback confidence

class TestHostGPT:
    """Test cases for HostGPT AI engine"""
    
    @pytest.fixture
    def host(self):
        return HostGPT()
    
    @pytest.mark.asyncio
    async def test_optimize_schedule(self, host):
        """Test schedule optimization"""
        hackathon_data = {
            'tasks': [
                {'id': 1, 'title': 'Task 1', 'duration': 2},
                {'id': 2, 'title': 'Task 2', 'duration': 3}
            ],
            'resources': {'staff': 2},
            'constraints': {'max_parallel': 2}
        }
        
        result = await host.optimize_schedule(hackathon_data)
        
        assert 'schedule' in result
        assert 'resource_allocation' in result
        assert 'risks' in result
        assert 'recommendations' in result
        
    @pytest.mark.asyncio
    async def test_assign_tasks_ai(self, host):
        """Test AI task assignment"""
        tasks = [
            {'id': 1, 'title': 'Task 1', 'skill': 'Python'},
            {'id': 2, 'title': 'Task 2', 'skill': 'Design'}
        ]
        team_members = [
            {'id': 1, 'name': 'Alice', 'skills': ['Python']},
            {'id': 2, 'name': 'Bob', 'skills': ['Design']}
        ]
        
        result = await host.assign_tasks_ai(tasks, team_members)
        
        assert isinstance(result, list)
        assert len(result) == len(tasks)

class TestAPI:
    """Test cases for API endpoints"""
    
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    @pytest.fixture
    def auth_token(self):
        # Create test user and get token
        return "test_token"
    
    def test_health_check(self, client):
        """Test health check endpoint"""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
    
    def test_auth_flow(self, client):
        """Test authentication flow"""
        # Register
        response = client.post("/auth/register", json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "password123",
            "full_name": "Test User",
            "role": "participant"
        })
        assert response.status_code == 200
        
        # Login
        response = client.post("/auth/token", data={
            "username": "testuser",
            "password": "password123"
        })
        assert response.status_code == 200
        assert "access_token" in response.json()
    
    def test_hackathon_crud(self, client):
        """Test hackathon CRUD operations"""
        # First login
        response = client.post("/auth/token", data={
            "username": "testuser",
            "password": "password123"
        })
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        # Create hackathon
        response = client.post("/hackathons", json={
            "title": "Test Hackathon",
            "description": "Test Description",
            "start_date": "2026-09-01T09:00:00",
            "end_date": "2026-09-03T18:00:00",
            "max_teams": 50
        }, headers=headers)
        assert response.status_code == 200
        hackathon_id = response.json()["hackathon_id"]
        
        # Get hackathon
        response = client.get(f"/hackathons/{hackathon_id}", headers=headers)
        assert response.status_code == 200
        assert response.json()["title"] == "Test Hackathon"
        
        # List hackathons
        response = client.get("/hackathons", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) >= 1

# ============================================
# DEPLOYMENT CONFIGURATIONS
# ============================================

# docker-compose.yml
"""
version: '3.8'

services:
  # PostgreSQL Database
  postgres:
    image: postgres:15
    environment:
      POSTGRES_USER: hacksupport
      POSTGRES_PASSWORD: secure_password
      POSTGRES_DB: hacksupport
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U hacksupport"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Redis for caching and pub/sub
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # FastAPI Backend
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql://hacksupport:secure_password@postgres/hacksupport
      REDIS_URL: redis://redis:6379
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      SECRET_KEY: ${SECRET_KEY}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./app:/app
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  # Celery Worker
  celery:
    build:
      context: .
      dockerfile: Dockerfile.backend
    environment:
      DATABASE_URL: postgresql://hacksupport:secure_password@postgres/hacksupport
      REDIS_URL: redis://redis:6379
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    depends_on:
      - postgres
      - redis
    command: celery -A microservices worker --loglevel=info

  # React Frontend
  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    ports:
      - "3000:3000"
    environment:
      REACT_APP_API_URL: http://localhost:8000
      REACT_APP_WS_URL: ws://localhost:8000
    depends_on:
      - backend
    volumes:
      - ./frontend:/app
      - /app/node_modules

  # Nginx for load balancing
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
      - ./ssl:/etc/nginx/ssl
    depends_on:
      - backend
      - frontend

volumes:
  postgres_data:
  redis_data:
"""

# Dockerfile.backend
"""
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run migrations
RUN alembic upgrade head

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

# Dockerfile.frontend
"""
FROM node:18-alpine

WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm install

# Copy application code
COPY . .

EXPOSE 3000

CMD ["npm", "start"]
"""

# nginx.conf
"""
events {
    worker_connections 1024;
}

http {
    upstream backend {
        server backend:8000;
    }

    upstream frontend {
        server frontend:3000;
    }

    server {
        listen 80;
        server_name hacksupport.pro;

        # Frontend
        location / {
            proxy_pass http://frontend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection 'upgrade';
            proxy_set_header Host $host;
            proxy_cache_bypass $http_upgrade;
        }

        # Backend API
        location /api/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection 'upgrade';
            proxy_set_header Host $host;
            proxy_cache_bypass $http_upgrade;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # WebSocket
        location /ws/ {
            proxy_pass http://backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "Upgrade";
            proxy_set_header Host $host;
        }
    }

    # HTTPS configuration (commented out, requires SSL certs)
    # server {
    #     listen 443 ssl;
    #     server_name hacksupport.pro;
    #     
    #     ssl_certificate /etc/nginx/ssl/cert.pem;
    #     ssl_certificate_key /etc/nginx/ssl/key.pem;
    #     
    #     # Rest of configuration...
    # }
}
"""

# requirements.txt
"""
fastapi==0.104.1
uvicorn[standard]==0.24.0
sqlalchemy==2.0.23
alembic==1.12.1
psycopg2-binary==2.9.9
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.6
redis==5.0.1
celery==5.3.4
aiohttp==3.8.6
openai==0.28.1
pydantic==2.5.0
pydantic-settings==2.1.0
scikit-learn==1.3.0
numpy==1.24.3
pytest==7.4.3
pytest-asyncio==0.21.1
websockets==12.0
redis==5.0.1
"""

# ============================================
# MONITORING - monitoring.py
# ============================================

import time
import psutil
import logging
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from fastapi import Response

logger = logging.getLogger(__name__)

# Metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'HTTP request latency', ['method', 'endpoint'])
ACTIVE_USERS = Gauge('active_users', 'Number of active users')
EVALUATION_COUNT = Counter('ai_evaluations_total', 'Total AI evaluations')
EVALUATION_TIME = Histogram('ai_evaluation_duration_seconds', 'AI evaluation duration')

class MonitoringService:
    """Service for monitoring and metrics collection"""
    
    def __init__(self):
        self.start_time = time.time()
        self.uptime = 0
        
    async def get_system_metrics(self):
        """Get system health metrics"""
        return {
            'uptime_seconds': time.time() - self.start_time,
            'cpu_percent': psutil.cpu_percent(interval=1),
            'memory_percent': psutil.virtual_memory().percent,
            'disk_usage_percent': psutil.disk_usage('/').percent,
            'active_connections': len(manager.active_connections.get('default', [])),
            'active_users': len(manager.user_connections)
        }
    
    async def get_application_metrics(self):
        """Get application-specific metrics"""
        return {
            'total_hackathons': 0,  # Would be fetched from DB
            'total_projects': 0,
            'total_evaluations': 0,
            'average_score': 0,
            'ai_accuracy': 95.5
        }

monitoring = MonitoringService()

@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    return Response(content=generate_latest(), media_type="text/plain")

@app.get("/health")
async def health_check():
    """Health check endpoint for load balancers"""
    metrics = await monitoring.get_system_metrics()
    return {
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'metrics': metrics
    }

# ============================================
# EVENT HANDLERS - event_handlers.py
# ============================================

class EventHandler:
    """Handle various events across the platform"""
    
    def __init__(self):
        self.handlers = {}
        
    def register(self, event_type: str, handler):
        """Register an event handler"""
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)
        
    async def trigger(self, event_type: str, data: dict):
        """Trigger an event and call all handlers"""
        if event_type in self.handlers:
            for handler in self.handlers[event_type]:
                await handler(data)
                
    async def on_project_submitted(self, project_id: int):
        """Handle project submission event"""
        # Trigger AI evaluation
        evaluate_project_async.delay(project_id, None)
        
        # Notify judges
        await manager.broadcast_to_channel('