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