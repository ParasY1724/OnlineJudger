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