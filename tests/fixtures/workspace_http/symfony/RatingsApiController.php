<?php

declare(strict_types=1);

namespace Instana\RobotShop\Ratings\Controller;

use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Annotation\Route;

/**
 * @Route("/api")
 */
class RatingsApiController
{
    /**
     * @Route(path="/rate/{sku}/{score}", methods={"PUT"})
     */
    public function put(Request $request, string $sku, int $score): Response
    {
        return new JsonResponse(["success" => true]);
    }

    /**
     * @Route("/fetch/{sku}", methods={"GET"})
     */
    public function get(Request $request, string $sku): Response
    {
        return new JsonResponse(["sku" => $sku]);
    }
}
