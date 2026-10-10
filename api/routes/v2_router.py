import logging

from fastapi import APIRouter, Depends

from api.services.auth import get_api_key, reject_usage_only_keys


def get_v2_router() -> APIRouter:
    logging.info("Initializing v2 API router")

    # Create V2 router with API key authentication dependency
    # All routes under /v2 will require valid API key (unless AUTH_DISABLED=true).
    # A read-only usage key (scope usage:read only) is refused here: it exists for the
    # /usage dashboard and must not be able to call agents.
    v2_router = APIRouter(prefix="/v2", dependencies=[Depends(get_api_key), Depends(reject_usage_only_keys)])

    # Include the v2 agents router
    logging.debug("Getting and including v2 agents router")
    from api.routes.v2.agents import v2_agents_router

    v2_router.include_router(v2_agents_router)
    logging.info("v2 agents router included successfully")

    # Include the v2 teams router
    logging.debug("Getting and including v2 teams router")
    from api.routes.v2.teams import v2_teams_router

    v2_router.include_router(v2_teams_router)
    logging.info("v2 teams router included successfully")

    # Include the v2 knowledge router
    logging.debug("Getting and including v2 knowledge router")
    from api.routes.v2.knowledge import router as knowledge_router

    v2_router.include_router(knowledge_router)
    logging.info("v2 knowledge router included successfully")

    # Include the v2 tokens router
    logging.debug("Getting and including v2 tokens router")
    from api.routes.v2.tokens import tokens_router

    v2_router.include_router(tokens_router)
    logging.info("v2 tokens router included successfully")

    # Include the v2 prompts router
    logging.debug("Getting and including v2 prompts router")
    from api.routes.v2.prompts import router as prompts_router

    v2_router.include_router(prompts_router)
    logging.info("v2 prompts router included successfully")

    # Include the v2 skills router
    logging.debug("Getting and including v2 skills router")
    from api.routes.v2.skills import v2_skills_router

    v2_router.include_router(v2_skills_router)
    logging.info("v2 skills router included successfully")

    # Include the v2 engines router
    from api.routes.v2.engines import v2_engines_router

    v2_router.include_router(v2_engines_router)
    logging.info("v2 engines router included successfully")

    # Include the v2 targets router
    from api.routes.v2.targets import v2_targets_router

    v2_router.include_router(v2_targets_router)
    logging.info("v2 targets router included successfully")

    # Include the v2 approvals router
    from api.routes.v2.approvals import v2_approvals_router

    v2_router.include_router(v2_approvals_router)
    logging.info("v2 approvals router included successfully")

    logging.info("v2 API router initialized successfully")
    return v2_router
