terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

resource "aws_dynamodb_table" "submissions" {
  name         = "CodeJudgeSubmissions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "submissionId"

  attribute {
    name = "submissionId"
    type = "S"
  }
}

resource "aws_sqs_queue" "submission_queue" {
  name = "codejudge-submission-queue"
  visibility_timeout_seconds = 30
}

resource "aws_sqs_queue" "result_queue" {
  name = "codejudge-result-queue"
}


resource "aws_vpc" "isolated_vpc" {
  cidr_block = "10.0.0.0/16"
  enable_dns_support = true
  enable_dns_hostnames = true
}

resource "aws_subnet" "isolated_subnet" {
  vpc_id = aws_vpc.isolated_vpc.id
  cidr_block = "10.0.1.0/24"
}

resource "aws_security_group" "lambda_sg" {
  name = "codejudge-lambda-sg"
  vpc_id = aws_vpc.isolated_vpc.id
}

data "aws_region" "current" {}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id       = aws_vpc.isolated_vpc.id
  service_name = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
}

resource "aws_iam_role" "lambda_exec_role" {
  name = "CodeJudgeLambdaExecRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}



data "aws_caller_identity" "current" {}

resource "aws_lambda_function" "judge_engine" {
  function_name = "CodeJudgeEngine"
  role          = aws_iam_role.lambda_exec_role.arn
  package_type  = "Image"

  image_uri = "${data.aws_caller_identity.current.account_id}.dkr.ecr.us-east-1.amazonaws.com/code-judge-lambda:latest"

  memory_size = 256
  timeout     = 10
}


resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.submission_queue.arn
  function_name    = aws_lambda_function.judge_engine.arn
  batch_size       = 1
}

resource "aws_iam_role_policy" "lambda_sqs_dynamo_policy" {
  role = aws_iam_role.lambda_exec_role.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      { Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Effect = "Allow", Resource = aws_sqs_queue.submission_queue.arn },
      { Action = "sqs:SendMessage", Effect = "Allow", Resource = aws_sqs_queue.result_queue.arn },
      { Action = ["dynamodb:UpdateItem", "dynamodb:PutItem", "dynamodb:GetItem"], Effect = "Allow", Resource = aws_dynamodb_table.submissions.arn }
    ]
  })
}


resource "aws_iam_role" "apigw_sqs_role" {
  name = "CodeJudgeApiGatewayRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "apigateway.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "apigw_sqs_policy" {
  role = aws_iam_role.apigw_sqs_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sqs:SendMessage"
      Resource = aws_sqs_queue.submission_queue.arn
    }]
  })
}

resource "aws_api_gateway_rest_api" "judge_api" { name = "CodeJudgeAPI" }

resource "aws_api_gateway_resource" "submit" {
  rest_api_id = aws_api_gateway_rest_api.judge_api.id
  parent_id   = aws_api_gateway_rest_api.judge_api.root_resource_id
  path_part   = "submit"
}

resource "aws_api_gateway_method" "submit_post" {
  rest_api_id   = aws_api_gateway_rest_api.judge_api.id
  resource_id   = aws_api_gateway_resource.submit.id
  http_method   = "POST"
  authorization = "NONE"
}

resource "aws_api_gateway_integration" "sqs_integration" {
  rest_api_id             = aws_api_gateway_rest_api.judge_api.id
  resource_id             = aws_api_gateway_resource.submit.id
  http_method             = aws_api_gateway_method.submit_post.http_method
  type                    = "AWS"
  integration_http_method = "POST"
  uri                     = "arn:aws:apigateway:${data.aws_region.current.name}:sqs:path/${data.aws_caller_identity.current.account_id}/${aws_sqs_queue.submission_queue.name}"
  credentials             = aws_iam_role.apigw_sqs_role.arn
  request_parameters      = { "integration.request.header.Content-Type" = "'application/x-www-form-urlencoded'" }
  request_templates       = { "application/json" = "Action=SendMessage&MessageBody=$util.urlEncode($input.body)" }
}

resource "aws_iam_role_policy_attachment" "lambda_basic_logs" {
  role       = aws_iam_role.lambda_exec_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_api_gateway_method_response" "response_200" {
  rest_api_id = aws_api_gateway_rest_api.judge_api.id
  resource_id = aws_api_gateway_resource.submit.id
  http_method = aws_api_gateway_method.submit_post.http_method
  status_code = "200"
}

resource "aws_api_gateway_integration_response" "sqs_integration_response" {
  rest_api_id = aws_api_gateway_rest_api.judge_api.id
  resource_id = aws_api_gateway_resource.submit.id
  http_method = aws_api_gateway_method.submit_post.http_method
  status_code = aws_api_gateway_method_response.response_200.status_code
  response_templates = { "application/json" = "{\"message\": \"Queued\", \"id\": \"$input.path('$.SendMessageResponse.SendMessageResult.MessageId')\"}" }
  depends_on = [aws_api_gateway_integration.sqs_integration]
}

resource "aws_api_gateway_deployment" "api_deployment" {
  rest_api_id = aws_api_gateway_rest_api.judge_api.id
  depends_on  = [aws_api_gateway_integration_response.sqs_integration_response]
  stage_name  = "v1"
}



# Callback Lambda (Processes Result Queue)

data "archive_file" "callback_zip" {
  type        = "zip"
  source_dir  = "${path.module}/callback"
  output_path = "${path.module}/callback.zip"
}

resource "aws_iam_role" "callback_lambda_role" {
  name = "CodeJudgeCallbackRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "callback_basic_logs" {
  role       = aws_iam_role.callback_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "callback_sqs_policy" {
  role = aws_iam_role.callback_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ]
      Effect   = "Allow"
      Resource = aws_sqs_queue.result_queue.arn
    }]
  })
}

resource "aws_lambda_function" "callback_engine" {
  function_name    = "CodeJudgeCallbackEngine"
  role             = aws_iam_role.callback_lambda_role.arn
  handler          = "callback.handler"
  runtime          = "python3.10" # Standard Python runtime
  
  # Use the zipped file created by Terraform
  filename         = data.archive_file.callback_zip.output_path
  source_code_hash = data.archive_file.callback_zip.output_base64sha256

  timeout          = 10
  memory_size      = 128

}
resource "aws_lambda_event_source_mapping" "callback_sqs_trigger" {
  event_source_arn = aws_sqs_queue.result_queue.arn
  function_name    = aws_lambda_function.callback_engine.arn
  batch_size       = 10 # It can process up to 10 results in a single Lambda execution to save costs
}