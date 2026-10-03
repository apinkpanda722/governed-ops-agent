"""컨테이너 한 프로세스에서 REST·관리 서버를 함께 실행한다."""

import argparse
import asyncio

import uvicorn

from govops.mocks.service import create_admin_app, create_rest_app


async def _serve(service: str) -> None:
    # 0.0.0.0 바인드는 컨테이너 안에서만 사용한다.
    rest = uvicorn.Server(
        uvicorn.Config(create_rest_app(service), host="0.0.0.0", port=8000)
    )
    admin = uvicorn.Server(
        uvicorn.Config(create_admin_app(service), host="0.0.0.0", port=9000)
    )
    await asyncio.gather(rest.serve(), admin.serve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("jira", "slack"))
    args = parser.parse_args(argv)
    asyncio.run(_serve(args.service))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
