import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ClipRangeForm, validateRange } from "../components/ClipRangeForm";

describe("validateRange", () => {
  it("requires non-negative start", () => {
    expect(validateRange(-1, 5, 100)).toMatch(/start/);
  });
  it("requires end > start", () => {
    expect(validateRange(5, 5, 100)).toMatch(/end/);
    expect(validateRange(5, 4, 100)).toMatch(/end/);
  });
  it("rejects start past duration", () => {
    expect(validateRange(120, 130, 100)).toMatch(/past end/);
  });
  it("accepts a valid range", () => {
    expect(validateRange(0, 5, 100)).toBeNull();
    expect(validateRange(0, 5, null)).toBeNull();
  });
});

describe("ClipRangeForm", () => {
  it("renders inputs and a submit button", () => {
    render(<ClipRangeForm onSubmit={() => {}} />);
    expect(screen.getByLabelText(/start \(seconds\)/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/end \(seconds\)/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /export clip/i })).toBeInTheDocument();
  });

  it("shows an alert when range is invalid", async () => {
    const onSubmit = vi.fn();
    render(<ClipRangeForm initialStart={5} initialEnd={5} onSubmit={onSubmit} />);
    await userEvent.click(screen.getByRole("button", { name: /export clip/i }));
    expect(screen.getByRole("alert")).toHaveTextContent(/end must be greater/);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("submits values for a valid range", async () => {
    const onSubmit = vi.fn();
    render(<ClipRangeForm initialStart={1} initialEnd={4} onSubmit={onSubmit} />);
    const labels = screen.getByLabelText(/labels/i);
    fireEvent.change(labels, { target: { value: "laughing,returned" } });
    await userEvent.click(screen.getByRole("button", { name: /export clip/i }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      start_seconds: 1,
      end_seconds: 4,
      labels: ["laughing", "returned"],
    });
  });
});
