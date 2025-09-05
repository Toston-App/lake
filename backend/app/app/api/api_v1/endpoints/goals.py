from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import crud, models, schemas
from app.api import deps
from app.models.goal import GoalStatus

router = APIRouter()


@router.get("/", response_model=List[schemas.Goal])
async def read_goals(
    db: AsyncSession = Depends(deps.async_get_db),
    skip: int = 0,
    limit: int = 100,
    status: Optional[GoalStatus] = None,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Retrieve goals for the current user.
    """
    if crud.user.is_superuser(current_user):
        goals = await crud.goal.get_multi(db, skip=skip, limit=limit)
    else:
        goals = await crud.goal.get_multi_by_owner(
            db=db, owner_id=current_user.id, skip=skip, limit=limit, status=status
        )
    return goals


@router.post("/", response_model=schemas.Goal)
async def create_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    goal_in: schemas.GoalCreate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Create new goal.
    """
    goal = await crud.goal.create_with_owner(
        db=db, obj_in=goal_in, owner_id=current_user.id
    )
    return goal


@router.get("/stats", response_model=schemas.GoalStats)
async def read_goal_stats(
    db: AsyncSession = Depends(deps.async_get_db),
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get goal statistics for the current user.
    """
    stats = await crud.goal.get_stats_by_owner(db=db, owner_id=current_user.id)
    return stats


@router.get("/{id}", response_model=schemas.Goal)
async def read_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Get goal by ID.
    """
    goal = await crud.goal.get_by_id_and_owner(
        db=db, id=id, owner_id=current_user.id
    )
    if not goal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
        )
    return goal


@router.put("/{id}", response_model=schemas.Goal)
async def update_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    goal_in: schemas.GoalUpdate,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Update a goal.
    """
    goal = await crud.goal.get_by_id_and_owner(
        db=db, id=id, owner_id=current_user.id
    )
    if not goal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
        )
    goal = await crud.goal.update_by_id_and_owner(
        db=db, id=id, owner_id=current_user.id, obj_in=goal_in
    )
    return goal


@router.post("/{id}/add", response_model=schemas.Goal)
async def add_to_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    amount: float,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Add money to a goal.
    """
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Amount must be positive"
        )
    
    goal = await crud.goal.add_to_goal(
        db=db, goal_id=id, owner_id=current_user.id, amount=amount
    )
    if not goal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
        )
    return goal


@router.delete("/{id}", response_model=schemas.DeletionResponse)
async def delete_goal(
    *,
    db: AsyncSession = Depends(deps.async_get_db),
    id: int,
    current_user: models.User = Depends(deps.get_current_active_user),
) -> Any:
    """
    Delete a goal.
    """
    goal = await crud.goal.get_by_id_and_owner(
        db=db, id=id, owner_id=current_user.id
    )
    if not goal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Goal not found"
        )
    await crud.goal.delete_by_id_and_owner(
        db=db, id=id, owner_id=current_user.id
    )
    return schemas.DeletionResponse(message="Goal deleted successfully")