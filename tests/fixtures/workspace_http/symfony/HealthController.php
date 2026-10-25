<?php

declare(strict_types=1);

namespace Instana\RobotShop\Ratings\Controller;

use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Annotation\Route;

/**
 * @Route("/_health")
 */
class HealthController
{
    public function __invoke(Request $request)
    {
        return new JsonResponse(["status" => "ok"], Response::HTTP_OK);
    }
}
