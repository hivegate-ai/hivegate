# Contributing to HiveGate

Thank you for your interest in contributing to HiveGate! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Pull Request Process](#pull-request-process)
- [Code Style](#code-style)
- [Testing](#testing)
- [Commit Messages](#commit-messages)

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to the maintainers.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/hivegate.git
   cd hivegate
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/hivegate-ai/hivegate.git
   ```

## Development Setup

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) package manager
- Docker and Docker Compose (for integration tests)

### Environment Setup

```bash
# Create virtual environment and install dependencies
./scripts/dev_setup.sh

# Activate the virtual environment
source .venv/bin/activate

# Install pre-commit hooks
pre-commit install
```

### Running the Application

```bash
# Start with Docker Compose (recommended)
docker compose up -d

# Or run directly
./scripts/start_server.sh
```

## Making Changes

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/your-bug-fix
   ```

2. **Make your changes** following our [code style guidelines](#code-style)

3. **Write or update tests** for your changes

4. **Run the validation suite**:
   ```bash
   ./scripts/run_validate.sh
   ```

5. **Commit your changes** following our [commit message format](#commit-messages)

## Pull Request Process

1. **Update documentation** if you're changing functionality
2. **Ensure all checks pass** (linting, type checking, tests)
3. **Write a clear PR description** explaining:
   - What changes you made
   - Why you made them
   - How to test them
4. **Request a review** from maintainers
5. **Address review feedback** promptly
6. **Squash commits** if requested

### PR Checklist

- [ ] Code follows the project's style guidelines
- [ ] Tests added/updated for changes
- [ ] Documentation updated if needed
- [ ] All CI checks pass
- [ ] Commit messages follow conventional format

## Code Style

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting, and [mypy](https://mypy-lang.org/) for type checking.

### Formatting

```bash
# Format code
./scripts/format.sh

# Check without modifying
uv run ruff format --check .
```

### Linting

```bash
# Lint with auto-fix
uv run ruff check --fix .

# Lint without auto-fix
uv run ruff check .
```

### Type Checking

```bash
# Run mypy
uv run mypy . --config-file pyproject.toml
```

### Style Guidelines

- **Line length**: 120 characters maximum
- **Imports**: Sorted automatically by ruff
- **Type hints**: Required for all public functions
- **Docstrings**: Use Google-style docstrings for public APIs
- **No module docstrings**: Skip `"""Module description."""` at file tops

## Testing

### Running Tests

```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/v2/test_agents.py

# Run with coverage
python -m pytest --cov

# Run only unit tests
python -m pytest -m unit

# Run only integration tests
python -m pytest -m integration
```

### Writing Tests

- Place tests in the `tests/` directory
- Use `tests/test_utils.py:create_test_client()` for API tests
- Mock external services (prompts service, cloud run)
- Follow existing patterns in the codebase

### Test Coverage

- Aim for 70%+ coverage on new code
- All new features should include tests
- Bug fixes should include regression tests

## Commit Messages

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `test`: Adding or updating tests
- `build`: Build system changes
- `ci`: CI/CD changes
- `chore`: Maintenance tasks
- `revert`: Reverting changes
- `deps`: Dependency updates

### Examples

```
feat(calendar): add Google Meet integration for calendar events

fix(tokens): handle expired OAuth refresh tokens gracefully

docs(api): add examples to knowledge API documentation

test(v2): add integration tests for team management
```

### Scope (Optional)

Common scopes include:
- `api`, `agents`, `db`, `toolkits`, `cli`
- Feature-specific: `calendar`, `email`, `tokens`, `knowledge`

## Questions?

- Open a [GitHub Discussion](https://github.com/hivegate-ai/hivegate/discussions) for questions
- Check existing [Issues](https://github.com/hivegate-ai/hivegate/issues) before reporting bugs
- Read the [documentation](docs/) for implementation details

Thank you for contributing! 🎉