<?php
require __DIR__ . '/../vendor/autoload.php';
use Slim\Factory\AppFactory;
$app = AppFactory::create();
$app->get('/', function ($req, $res) {
    $res->getBody()->write(json_encode([
        "msg" => "DevOps Journey Day1 - PHP",
        "framework" => "slim"
    ]));
    return $res->withHeader('Content-Type', 'application/json');
});
$app->run();
