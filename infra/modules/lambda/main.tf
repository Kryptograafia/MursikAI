resource "aws_cloudwatch_log_group" "assistant" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = var.log_retention_days
}

data "archive_file" "source" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.module}/build/assistant.zip"
}

resource "aws_lambda_function" "assistant" {
  function_name = var.function_name
  role          = aws_iam_role.assistant.arn
  runtime       = "python3.13"
  architectures = ["arm64"]
  handler       = "handler.lambda_handler"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.source.output_path
  source_code_hash = data.archive_file.source.output_base64sha256

  environment {
    variables = {
      TABLE_NAME      = var.table_name
      MODEL_ID        = var.model_id
      MAX_TOOL_ROUNDS = "4"
    }
  }

  depends_on = [aws_cloudwatch_log_group.assistant]
}