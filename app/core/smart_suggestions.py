"""
Smart Suggestions Service - Learns from user behavior and provides intelligent suggestions
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from collections import Counter

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_behavior import UserBehavior, UserPreference, SmartSuggestion
from app.models.task import Task
from app.models.ticket import Ticket
from app.models.assignment import Assignment
from app.models.user import User

logger = logging.getLogger(__name__)


async def track_user_action(
    db: AsyncSession,
    user_id: int,
    workspace_id: int,
    action_type: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    project_id: Optional[int] = None,
    field_name: Optional[str] = None,
    field_value: Optional[str] = None,
    context_data: Optional[dict] = None
):
    """
    Record a user action for learning purposes.
    Call this whenever a user does something significant.
    """
    if not workspace_id:
        return  # the super admin with no company: nothing to learn for
    try:
        now = datetime.utcnow()
        behavior = UserBehavior(
            user_id=user_id,
            workspace_id=workspace_id,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            project_id=project_id,
            field_name=field_name,
            field_value=field_value,
            context_data=json.dumps(context_data) if context_data else None,
            hour_of_day=now.hour,
            day_of_week=now.weekday()
        )
        db.add(behavior)
        # Don't commit here - let the caller handle transaction
    except Exception as e:
        logger.error(f"Failed to track user action: {e}")


async def get_suggested_assignees(
    db: AsyncSession,
    user_id: int,
    workspace_id: int,
    project_id: Optional[int] = None,
    limit: int = 3
) -> List[Dict[str, Any]]:
    """
    Get suggested assignees based on user's past assignment patterns.
    """
    suggestions = []
    
    try:
        # Get user's recent assignment patterns
        query = select(
            UserBehavior.field_value,
            func.count(UserBehavior.id).label('count')
        ).where(
            UserBehavior.user_id == user_id,
            UserBehavior.workspace_id == workspace_id,
            UserBehavior.action_type == 'task_assigned',
            UserBehavior.field_name == 'assignee_id',
            UserBehavior.created_at >= datetime.utcnow() - timedelta(days=30)
        )
        
        if project_id:
            query = query.where(UserBehavior.project_id == project_id)
        
        query = query.group_by(UserBehavior.field_value).order_by(func.count(UserBehavior.id).desc()).limit(limit)
        
        result = await db.execute(query)
        rows = result.fetchall()
        
        for row in rows:
            assignee_id = row[0]
            count = row[1]
            if assignee_id:
                # Get user details
                user = (await db.execute(
                    select(User).where(User.id == int(assignee_id))
                )).scalar_one_or_none()
                
                if user:
                    suggestions.append({
                        'user_id': user.id,
                        'name': user.shown_name or user.username,
                        'email': user.email,
                        'frequency': count,
                        'confidence': min(count / 10, 1.0)  # Cap at 100%
                    })
    except Exception as e:
        logger.error(f"Error getting suggested assignees: {e}")
    
    return suggestions


async def get_suggested_priority(
    db: AsyncSession,
    user_id: int,
    workspace_id: int,
    project_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Get suggested priority based on user's past patterns.
    """
    try:
        query = select(
            UserBehavior.field_value,
            func.count(UserBehavior.id).label('count')
        ).where(
            UserBehavior.user_id == user_id,
            UserBehavior.workspace_id == workspace_id,
            UserBehavior.action_type == 'task_created',
            UserBehavior.field_name == 'priority',
            UserBehavior.created_at >= datetime.utcnow() - timedelta(days=30)
        )
        
        if project_id:
            query = query.where(UserBehavior.project_id == project_id)
        
        query = query.group_by(UserBehavior.field_value).order_by(func.count(UserBehavior.id).desc()).limit(1)
        
        result = await db.execute(query)
        row = result.fetchone()
        
        if row and row[0]:
            return {
                'priority': row[0],
                'frequency': row[1],
                'confidence': min(row[1] / 10, 1.0)
            }
    except Exception as e:
        logger.error(f"Error getting suggested priority: {e}")
    
    return None


async def get_similar_tasks(
    db: AsyncSession,
    user_id: int,
    workspace_id: int,
    title_keywords: str,
    project_id: Optional[int] = None,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Find similar tasks based on title keywords.
    """
    similar = []
    
    try:
        # Simple keyword matching - split title into words
        keywords = [w.lower() for w in title_keywords.split() if len(w) > 2]
        
        if not keywords:
            return []
        
        # Get recent tasks from this user
        query = select(Task).join(
            Assignment, Task.id == Assignment.task_id, isouter=True
        ).where(
            Task.creator_id == user_id
        ).order_by(Task.created_at.desc()).limit(100)
        
        if project_id:
            query = query.where(Task.project_id == project_id)
        
        result = await db.execute(query)
        tasks = result.scalars().all()
        
        # Score tasks by keyword matches
        scored_tasks = []
        for task in tasks:
            task_words = [w.lower() for w in task.title.split() if len(w) > 2]
            matches = sum(1 for kw in keywords if kw in task_words)
            if matches > 0:
                scored_tasks.append({
                    'task_id': task.id,
                    'title': task.title,
                    'description': task.description[:100] if task.description else None,
                    'priority': task.priority,
                    'status': task.status,
                    'score': matches / len(keywords)
                })
        
        # Sort by score and return top matches
        scored_tasks.sort(key=lambda x: x['score'], reverse=True)
        similar = scored_tasks[:limit]
        
    except Exception as e:
        logger.error(f"Error finding similar tasks: {e}")
    
    return similar


async def get_work_pattern_insights(
    db: AsyncSession,
    user_id: int,
    workspace_id: int
) -> Dict[str, Any]:
    """
    Analyze user's work patterns and return insights.
    """
    insights = {
        'most_productive_hours': [],
        'most_productive_days': [],
        'average_task_duration': None,
        'preferred_priorities': [],
        'task_completion_rate': None
    }
    
    try:
        # Most productive hours (when they complete tasks)
        hour_query = select(
            UserBehavior.hour_of_day,
            func.count(UserBehavior.id).label('count')
        ).where(
            UserBehavior.user_id == user_id,
            UserBehavior.workspace_id == workspace_id,
            UserBehavior.action_type.in_(['task_completed', 'task_created']),
            UserBehavior.created_at >= datetime.utcnow() - timedelta(days=30)
        ).group_by(UserBehavior.hour_of_day).order_by(func.count(UserBehavior.id).desc()).limit(3)
        
        hour_result = await db.execute(hour_query)
        insights['most_productive_hours'] = [
            {'hour': row[0], 'activity_count': row[1]}
            for row in hour_result.fetchall()
        ]
        
        # Most productive days
        day_query = select(
            UserBehavior.day_of_week,
            func.count(UserBehavior.id).label('count')
        ).where(
            UserBehavior.user_id == user_id,
            UserBehavior.workspace_id == workspace_id,
            UserBehavior.action_type.in_(['task_completed', 'task_created']),
            UserBehavior.created_at >= datetime.utcnow() - timedelta(days=30)
        ).group_by(UserBehavior.day_of_week).order_by(func.count(UserBehavior.id).desc()).limit(3)
        
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_result = await db.execute(day_query)
        insights['most_productive_days'] = [
            {'day': day_names[row[0]], 'activity_count': row[1]}
            for row in day_result.fetchall()
        ]
        
        # Preferred priorities
        priority_query = select(
            UserBehavior.field_value,
            func.count(UserBehavior.id).label('count')
        ).where(
            UserBehavior.user_id == user_id,
            UserBehavior.workspace_id == workspace_id,
            UserBehavior.field_name == 'priority',
            UserBehavior.created_at >= datetime.utcnow() - timedelta(days=30)
        ).group_by(UserBehavior.field_value).order_by(func.count(UserBehavior.id).desc())
        
        priority_result = await db.execute(priority_query)
        insights['preferred_priorities'] = [
            {'priority': row[0], 'count': row[1]}
            for row in priority_result.fetchall()
        ]
        
    except Exception as e:
        logger.error(f"Error analyzing work patterns: {e}")
    
    return insights


async def get_smart_suggestions_for_user(
    db: AsyncSession,
    user_id: int,
    workspace_id: int,
    context_type: Optional[str] = None,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Get all active smart suggestions for a user.
    """
    suggestions = []
    
    try:
        query = select(SmartSuggestion).where(
            SmartSuggestion.user_id == user_id,
            SmartSuggestion.workspace_id == workspace_id,
            SmartSuggestion.is_active == True
        )
        
        if context_type:
            query = query.where(SmartSuggestion.context_type == context_type)
        
        query = query.order_by(SmartSuggestion.relevance_score.desc()).limit(limit)
        
        result = await db.execute(query)
        for sugg in result.scalars().all():
            suggestions.append({
                'id': sugg.id,
                'type': sugg.suggestion_type,
                'context': sugg.context_type,
                'data': json.loads(sugg.suggestion_data) if sugg.suggestion_data else {},
                'relevance': sugg.relevance_score,
                'acceptance_rate': sugg.times_accepted / max(sugg.times_shown, 1)
            })
            
            # Update times_shown
            sugg.times_shown += 1
        
    except Exception as e:
        logger.error(f"Error getting smart suggestions: {e}")
    
    return suggestions


async def record_suggestion_feedback(
    db: AsyncSession,
    suggestion_id: int,
    accepted: bool
):
    """
    Record whether a user accepted or dismissed a suggestion.
    """
    try:
        result = await db.execute(
            select(SmartSuggestion).where(SmartSuggestion.id == suggestion_id)
        )
        suggestion = result.scalar_one_or_none()
        
        if suggestion:
            if accepted:
                suggestion.times_accepted += 1
            else:
                suggestion.times_dismissed += 1
            
            # Deactivate suggestions that are frequently dismissed
            if suggestion.times_dismissed > 5 and suggestion.times_accepted == 0:
                suggestion.is_active = False
                
    except Exception as e:
        logger.error(f"Error recording suggestion feedback: {e}")


async def generate_task_templates_from_history(
    db: AsyncSession,
    user_id: int,
    workspace_id: int,
    project_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Generate task templates based on user's frequently created task patterns.
    """
    templates = []
    
    try:
        # Get user's recent tasks
        query = select(Task).where(
            Task.creator_id == user_id
        ).order_by(Task.created_at.desc()).limit(50)
        
        if project_id:
            query = query.where(Task.project_id == project_id)
        
        result = await db.execute(query)
        tasks = result.scalars().all()
        
        # Find common patterns in titles (simple approach)
        title_prefixes = Counter()
        for task in tasks:
            words = task.title.split()
            if len(words) >= 2:
                prefix = ' '.join(words[:2])
                title_prefixes[prefix] += 1
        
        # Create templates for common prefixes
        for prefix, count in title_prefixes.most_common(5):
            if count >= 2:  # At least 2 similar tasks
                templates.append({
                    'title_prefix': prefix,
                    'count': count,
                    'suggested_title': f"{prefix}..."
                })
        
    except Exception as e:
        logger.error(f"Error generating task templates: {e}")
    
    return templates
