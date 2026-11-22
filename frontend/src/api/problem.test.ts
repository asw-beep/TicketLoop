import { describe, expect, it } from "vitest";
import { ApiError, describeError, fieldErrors, problemCode, type Problem } from "./problem";

const validation: Problem = {
  type: "urn:ticketloop:problem:validation-error",
  title: "Request validation failed",
  status: 422,
  detail: "One or more fields are missing or invalid.",
  errors: [
    {
      location: ["body", "password"],
      message: "String should have at least 12 characters",
      type: "string_too_short",
    },
    { location: ["body", "email"], message: "value is not a valid email address", type: "x" },
  ],
};

describe("problem helpers", () => {
  it("reads the code from the problem type", () => {
    expect(problemCode(validation)).toBe("validation-error");
  });

  it("maps validation errors onto the form's field names", () => {
    expect(fieldErrors(validation)).toEqual({
      password: "String should have at least 12 characters",
      email: "value is not a valid email address",
    });
  });

  it("describes an API error by its detail, or else its title", () => {
    expect(describeError(new ApiError(validation))).toBe(
      "One or more fields are missing or invalid.",
    );
    expect(
      describeError(new ApiError({ type: "x", title: "Wrong email or password", status: 401 })),
    ).toBe("Wrong email or password");
  });

  it("says plainly when the API could not be reached", () => {
    expect(describeError(new TypeError("Failed to fetch"))).toMatch(/reach the TicketLoop API/);
  });
});
