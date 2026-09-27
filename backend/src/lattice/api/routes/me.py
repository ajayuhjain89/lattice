from fastapi import APIRouter

from lattice.api.deps import CurrentPrincipal
from lattice.api.schemas import MeResponse
from lattice.errors import ErrorResponse

router = APIRouter(tags=["me"])


@router.get(
    "/v1/me",
    response_model=MeResponse,
    responses={401: {"model": ErrorResponse}},
    summary="The signed-in user, their organization and capabilities",
)
async def me(principal: CurrentPrincipal) -> MeResponse:
    return MeResponse.from_principal(principal)
